from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import anthropic
except ImportError as e:
    raise ImportError("anthropic is required: pip install anthropic") from e

__all__ = ["BatchManager", "BatchManagerResult", "DEFAULT_STATE_DIR"]

_MAX_RETRIES = 3
_POLL_INTERVAL = 30        # seconds between Batch API status polls
_MAX_POLLS = 240           # 240 × 30s = 120 min max wait per batch
# NOTE: filter.py defines identical constants (_MAX_RETRIES, _POLL_INTERVAL, _MAX_POLLS)
# in its own internal _BatchState. If changing these values, update filter.py too.

DEFAULT_STATE_DIR = Path.home() / ".keyword_labeler" / "state"

# Type aliases for the two callbacks required by BatchManager.run().
# RequestBuilder: given a list of pending chunk indices, returns one Anthropic
#   request dict per index (same order), each shaped:
#   {"custom_id": str, "params": {"model": ..., "max_tokens": ..., ...}}
# ResultParser: given raw model response text, returns a dict to store in state.
#   Must never raise — use a safe fallback for unexpected output.
RequestBuilder = Callable[[list[int]], list[dict]]
ResultParser = Callable[[str], dict]


@dataclass
class BatchManagerResult:
    completed_count: int          # chunks that finished successfully
    retry_exhausted_count: int    # chunks where all retries failed
    results: dict[int, dict]      # chunk_index → parsed result dict


class BatchManager:
    """
    Manages Anthropic Batch API jobs with JSON state persistence and auto-retry.

    A "job" is split into chunks; each chunk produces one request inside an
    Anthropic batch.  All pending chunks are submitted in a single batch.create()
    call to minimise API round-trips, then polled until the batch ends.

    State is persisted to a JSON file so the job resumes from where it left off
    after a crash or MCP server restart.  On resume, version / job_name /
    num_chunks are validated to detect stale or mismatched files.

    Typical usage:
        path = DEFAULT_STATE_DIR / "grouper_sua_bot.json"
        if path.exists():
            bm = BatchManager.resume(path, job_name="grouper_sua_bot", num_chunks=20)
        else:
            bm = BatchManager.create(
                path,
                job_name="grouper_sua_bot",
                chunk_payloads=[{"kw_indices": [...]} for ...],
            )
        bm.run(client, build_requests=my_builder, parse_result=my_parser)
        result = bm.get_results()

    State file schema (JSON):
        version:    int
        job_name:   str
        num_chunks: int
        chunks: list of:
            index:       int
            payload:     dict   — opaque per-chunk data (e.g. {"kw_indices": [...]})
            retry_count: int
            batch_id:    str | null
            custom_id:   str | null
            done:        bool
            result:      dict | null
    """

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict = {}

    # ------------------------------------------------------------------ #
    # Factory methods
    # ------------------------------------------------------------------ #

    @classmethod
    def create(
        cls,
        path: Path,
        job_name: str,
        chunk_payloads: list[dict],
    ) -> "BatchManager":
        """
        Create a new job and write initial state to disk.

        Args:
            path:           path to the JSON state file; parent dir is created if needed
            job_name:       unique identifier validated on resume (e.g. "grouper_sua_bot")
            chunk_payloads: opaque per-chunk data; accessible later via
                            get_chunk_payload(index)
        """
        bm = cls(path)
        bm._data = {
            "version": cls.VERSION,
            "job_name": job_name,
            "num_chunks": len(chunk_payloads),
            "chunks": [
                {
                    "index": i,
                    "payload": payload,
                    "retry_count": 0,
                    "batch_id": None,
                    "custom_id": None,
                    "done": False,
                    "result": None,
                }
                for i, payload in enumerate(chunk_payloads)
            ],
        }
        bm._save()
        return bm

    @classmethod
    def resume(
        cls,
        path: Path,
        job_name: str,
        num_chunks: int,
    ) -> "BatchManager":
        """
        Load an existing state file and validate it matches the current job.

        Raises ValueError if version, job_name, or num_chunks do not match
        (indicates a stale or wrong state file — instruct user to delete it).
        """
        if not path.exists():
            raise FileNotFoundError(
                f"State file không tìm thấy: {path}. "
                "Dùng BatchManager.create() để khởi tạo job mới."
            )
        bm = cls(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if data.get("version") != cls.VERSION:
            raise ValueError(
                f"State file {path} version={data.get('version')}, "
                f"cần version={cls.VERSION}. Xóa để chạy lại từ đầu."
            )
        if data.get("job_name") != job_name:
            raise ValueError(
                f"State file {path} thuộc job '{data.get('job_name')}', "
                f"khác job hiện tại '{job_name}'. Dùng state_path khác."
            )
        if data.get("num_chunks") != num_chunks:
            raise ValueError(
                f"State file {path} có {data.get('num_chunks')} chunks, "
                f"khác num_chunks hiện tại {num_chunks}. Xóa để chạy lại."
            )
        bm._data = data
        return bm

    # ------------------------------------------------------------------ #
    # State accessors (internal)
    # ------------------------------------------------------------------ #

    @property
    def _chunks(self) -> list[dict]:
        return self._data["chunks"]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _needs_submission(self) -> list[dict]:
        return [
            c for c in self._chunks
            if not c["done"]
            and c["batch_id"] is None
            and c["retry_count"] < _MAX_RETRIES
        ]

    def _in_flight(self) -> list[dict]:
        return [c for c in self._chunks if not c["done"] and c["batch_id"] is not None]

    def _in_flight_batch_ids(self) -> set[str]:
        return {c["batch_id"] for c in self._in_flight() if c["batch_id"]}

    # ------------------------------------------------------------------ #
    # Public state queries
    # ------------------------------------------------------------------ #

    def is_complete(self) -> bool:
        """True when every chunk is either done or has exhausted all retries."""
        return all(c["done"] or c["retry_count"] >= _MAX_RETRIES for c in self._chunks)

    def get_chunk_payload(self, chunk_index: int) -> dict:
        """Return the opaque payload for a chunk (e.g. {"kw_indices": [...]})."""
        return self._chunks[chunk_index]["payload"]

    def get_completed_chunk_indices(self) -> list[int]:
        """Return indices of chunks that have completed successfully."""
        return [c["index"] for c in self._chunks if c["done"]]

    def get_failed_chunk_indices(self) -> list[int]:
        """Return indices of chunks that exhausted all retries without succeeding."""
        return [
            c["index"] for c in self._chunks
            if not c["done"] and c["retry_count"] >= _MAX_RETRIES
        ]

    @classmethod
    def resume_if_exists(
        cls,
        path: Path,
        job_name: str,
    ) -> "BatchManager | None":
        """
        Return a BatchManager if a valid state file exists for this job, else None.

        Simpler alternative to resume() when the caller does not know num_chunks
        upfront — the value is read from the state file itself.

        Raises ValueError if the file exists but has mismatched version or job_name.
        """
        if not path.exists():
            return None
        bm = cls(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != cls.VERSION:
            raise ValueError(
                f"State file {path} version={data.get('version')}, "
                f"cần version={cls.VERSION}. Xóa để chạy lại từ đầu."
            )
        if data.get("job_name") != job_name:
            raise ValueError(
                f"State file {path} thuộc job '{data.get('job_name')}', "
                f"khác job hiện tại '{job_name}'. Dùng state_path khác."
            )
        bm._data = data
        return bm

    # ------------------------------------------------------------------ #
    # Batch API operations (internal)
    # ------------------------------------------------------------------ #

    def _submit_pending(
        self,
        client: anthropic.Anthropic,
        build_requests: RequestBuilder,
    ) -> str:
        """
        Call build_requests with all pending chunk indices, submit as one batch.
        Returns the new batch_id.

        Raises ValueError if build_requests returns a different number of requests
        than pending chunks.
        """
        pending = self._needs_submission()
        pending_indices = [c["index"] for c in pending]
        requests = build_requests(pending_indices)

        if len(requests) != len(pending):
            raise ValueError(
                f"build_requests returned {len(requests)} request(s) for "
                f"{len(pending)} pending chunk(s). "
                "Must return exactly one request per chunk index, in the same order."
            )

        batch = client.messages.batches.create(requests=requests)
        batch_id = batch.id

        for chunk, req in zip(pending, requests):
            chunk["batch_id"] = batch_id
            chunk["custom_id"] = req["custom_id"]
        self._save()

        return batch_id

    def _poll_until_ended(self, client: anthropic.Anthropic, batch_id: str) -> None:
        """Block until batch processing_status == 'ended'. Raises TimeoutError after 2 hours."""
        for _ in range(_MAX_POLLS):
            batch = client.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                return
            time.sleep(_POLL_INTERVAL)
        raise TimeoutError(
            f"Batch {batch_id} chưa hoàn thành sau {_MAX_POLLS * _POLL_INTERVAL // 60} phút. "
            "Chạy lại để resume từ state file."
        )

    def _collect_results(
        self,
        client: anthropic.Anthropic,
        batch_id: str,
        parse_result: ResultParser,
    ) -> None:
        """
        Stream results for one batch; mark chunks done or queue for retry.

        If parse_result raises for a succeeded chunk, that chunk is treated as
        failed and will be retried (up to _MAX_RETRIES). State is saved once
        after all results are processed.
        """
        custom_id_to_chunk: dict[str, dict] = {
            c["custom_id"]: c
            for c in self._in_flight()
            if c["batch_id"] == batch_id and c["custom_id"]
        }

        for result in client.messages.batches.results(batch_id):
            chunk = custom_id_to_chunk.get(result.custom_id)
            if chunk is None:
                continue

            if result.result.type == "succeeded":
                text = next(
                    (b.text for b in result.result.message.content if hasattr(b, "text")),
                    "",
                )
                try:
                    parsed = parse_result(text)
                    chunk["done"] = True
                    chunk["result"] = parsed
                except Exception:
                    # parse_result raised → treat as failure and retry
                    chunk["retry_count"] += 1
                    chunk["batch_id"] = None
                    chunk["custom_id"] = None
            else:
                chunk["retry_count"] += 1
                chunk["batch_id"] = None
                chunk["custom_id"] = None

        self._save()

    # ------------------------------------------------------------------ #
    # Public run loop
    # ------------------------------------------------------------------ #

    def run(
        self,
        client: anthropic.Anthropic,
        build_requests: RequestBuilder,
        parse_result: ResultParser,
        on_round_complete: Callable[[BatchManagerResult], None] | None = None,
    ) -> None:
        """
        Execute the submit → poll → collect → retry loop until all chunks are
        done or max retries are exhausted.

        `build_requests` is called fresh each round so the caller can inject
        updated context between rounds.  For grouper Phase 5b: the caller
        captures top-50 groups in a nonlocal variable, updates it inside
        `on_round_complete`, and `build_requests` closes over it — this is
        how top-50 injection across rounds works.

        `parse_result(text)` converts raw model output to a dict stored in the
        state file.  If it raises, the chunk is retried.  For expected model
        output, prefer returning a safe fallback dict rather than raising.

        `on_round_complete(partial_result)` is called after each round's results
        are collected and saved.  Use it to update shared state (e.g. top-50
        groups) that `build_requests` will read in the next round.

        Args:
            client:             pre-built Anthropic client
            build_requests:     callable(list[chunk_index]) → list[request_dict]
                                called with pending indices; must return one request
                                per index in the same order; each request shaped:
                                {"custom_id": str, "params": {"model": ..., ...}}
            parse_result:       callable(str) → dict
            on_round_complete:  optional callable(BatchManagerResult) → None;
                                called after each round completes
        """
        # Resume any in-flight batches left over from a previous crash
        for batch_id in self._in_flight_batch_ids():
            self._poll_until_ended(client, batch_id)
            self._collect_results(client, batch_id, parse_result)
            if on_round_complete is not None:
                on_round_complete(self.get_results())

        # Main loop: submit all pending → poll → collect → repeat
        while not self.is_complete():
            if not self._needs_submission():
                break  # remaining chunks have exhausted all retries
            batch_id = self._submit_pending(client, build_requests)
            self._poll_until_ended(client, batch_id)
            self._collect_results(client, batch_id, parse_result)
            if on_round_complete is not None:
                on_round_complete(self.get_results())

    # ------------------------------------------------------------------ #
    # Results
    # ------------------------------------------------------------------ #

    def get_results(self) -> BatchManagerResult:
        """
        Return aggregate results.  Safe to call before run() completes for
        partial results (e.g. for a partial export when retries are exhausted).
        """
        results: dict[int, dict] = {
            c["index"]: c["result"]
            for c in self._chunks
            if c["done"] and c["result"] is not None
        }
        retry_exhausted_count = sum(
            1 for c in self._chunks
            if not c["done"] and c["retry_count"] >= _MAX_RETRIES
        )
        return BatchManagerResult(
            completed_count=len(results),
            retry_exhausted_count=retry_exhausted_count,
            results=results,
        )
