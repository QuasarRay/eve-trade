from __future__ import annotations

import copy
import io
import struct
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validate_nsq_configuration import (
    NSQConfigurationError,
    load_repository_state,
    parse_go_subscriptions,
    parse_go_topics,
    validate_backend_bindings,
    validate_repository,
)
from verify_nsq_restart_delivery import NSQCanaryError, parse_message_frame, read_frame


ROOT = Path(__file__).resolve().parents[2]


class NSQConfigurationTests(unittest.TestCase):
    def test_current_repository_configuration_is_internally_consistent(self) -> None:
        state = validate_repository(ROOT)
        self.assertEqual(state.topics["WorkTopic"], "settlement-work")
        self.assertEqual(
            state.subscriptions["trade-settlement-executor"].topic_symbol,
            "settlement.WorkTopic",
        )

    def test_work_channel_ephemeral_mutation_is_rejected(self) -> None:
        state = load_repository_state(ROOT)
        backend = copy.deepcopy(state.production_backend)
        topic = backend["topics"]["settlement-work"]
        subscription = topic["subscriptions"].pop("trade-settlement-executor")
        subscription["name"] = "trade-settlement-executor#ephemeral"
        topic["subscriptions"][subscription["name"]] = subscription
        with self.assertRaises(NSQConfigurationError):
            validate_backend_bindings(backend)

    def test_result_channel_ephemeral_mutation_is_rejected(self) -> None:
        state = load_repository_state(ROOT)
        backend = copy.deepcopy(state.production_backend)
        topic = backend["topics"]["settlement-results"]
        subscription = topic["subscriptions"].pop("market-settlement-result-projection")
        subscription["name"] = "market-settlement-result-projection#ephemeral"
        topic["subscriptions"][subscription["name"]] = subscription
        with self.assertRaises(NSQConfigurationError):
            validate_backend_bindings(backend)

    def test_structured_go_reader_observes_topic_and_retry_mutations(self) -> None:
        topic_source = (ROOT / "distributed-backend/src/settlement/work.go").read_text(encoding="utf-8")
        mutated_topics = parse_go_topics(topic_source.replace('"settlement-work"', '"wrong-work"', 1))
        self.assertEqual(mutated_topics["WorkTopic"], "wrong-work")

        worker_source = (ROOT / "distributed-backend/src/settlementworker/service.go").read_text(encoding="utf-8")
        subscriptions = parse_go_subscriptions(
            worker_source.replace(
                "settlementWorkerMinBackoff  = 2 * time.Second",
                "settlementWorkerMinBackoff  = 3 * time.Minute",
                1,
            )
        )
        work = subscriptions["trade-settlement-executor"]
        self.assertGreater(work.min_backoff_ns, work.max_backoff_ns)


class NSQProtocolCanaryTests(unittest.TestCase):
    def test_message_frame_parser_preserves_independent_identity_and_body(self) -> None:
        message_id = b"0123456789abcdef"
        payload = struct.pack(">QH", 123, 2) + message_id + b"payload"
        message = parse_message_frame(payload)
        self.assertEqual(message.timestamp, 123)
        self.assertEqual(message.attempts, 2)
        self.assertEqual(message.message_id, message_id)
        self.assertEqual(message.body, b"payload")

    def test_protocol_reader_rejects_oversized_or_truncated_frames(self) -> None:
        with self.assertRaises(NSQCanaryError):
            read_frame(io.BytesIO(struct.pack(">I", 20_000_000)))
        with self.assertRaises(NSQCanaryError):
            read_frame(io.BytesIO(struct.pack(">II", 12, 0) + b"short"))


if __name__ == "__main__":
    unittest.main()
