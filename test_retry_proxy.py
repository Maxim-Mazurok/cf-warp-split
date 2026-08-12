from __future__ import annotations

import importlib.machinery
import importlib.util
import socket
import struct
import unittest
from pathlib import Path
from unittest import mock

MODULE_NAME = "retry_proxy"
SOURCE_PATH = Path(__file__).with_name("retry-proxy")
MODULE_LOADER = importlib.machinery.SourceFileLoader(MODULE_NAME, str(SOURCE_PATH))
MODULE_SPECIFICATION = importlib.util.spec_from_loader(MODULE_NAME, MODULE_LOADER)
if MODULE_SPECIFICATION is None:
    raise RuntimeError(f"Could not load module specification for {SOURCE_PATH}")
retry_proxy = importlib.util.module_from_spec(MODULE_SPECIFICATION)
MODULE_LOADER.exec_module(retry_proxy)

PUBLIC_UPSTREAMS = (("1.1.1.1", 53), ("8.8.8.8", 53))


def build_query(query_type: int = 1) -> bytes:
    query_name = b"\x04host\x07example\x03com\x00"
    return b"".join(
        (
            struct.pack("!HHHHHH", 1, 0x0100, 1, 0, 0, 0),
            query_name,
            struct.pack("!HH", query_type, 1),
        )
    )


def build_response(query: bytes, response_code: int, answer_count: int = 0) -> bytes:
    response_flags = 0x8000 | 0x0080 | response_code
    return b"".join(
        (
            query[:2],
            struct.pack("!H", response_flags),
            query[4:6],
            struct.pack("!H", answer_count),
            b"\x00\x00\x00\x00\x00\x00",
            query[12:],
        )
    )


class ResolveTest(unittest.TestCase):
    def test_warp_only_returns_nxdomain_without_public_query(self) -> None:
        query = build_query()
        warp_response = build_response(query, response_code=3)

        with mock.patch.object(retry_proxy, "forward_once", return_value=warp_response) as forward_once:
            response = retry_proxy.resolve(query)

        self.assertEqual(warp_response, response)
        forward_once.assert_called_once_with(retry_proxy.WARP_UPSTREAMS[0], query, retry_proxy.UPSTREAM_TIMEOUT_SECONDS)

    def test_warp_first_returns_warp_answer_without_public_query(self) -> None:
        query = build_query()
        warp_response = build_response(query, response_code=0, answer_count=1)

        with mock.patch.object(retry_proxy, "forward_once", return_value=warp_response) as forward_once:
            response = retry_proxy.resolve(query, PUBLIC_UPSTREAMS)

        self.assertEqual(warp_response, response)
        forward_once.assert_called_once_with(retry_proxy.WARP_UPSTREAMS[0], query, retry_proxy.UPSTREAM_TIMEOUT_SECONDS)

    def test_warp_first_falls_back_after_nxdomain(self) -> None:
        query = build_query()
        warp_response = build_response(query, response_code=3)
        public_response = build_response(query, response_code=0, answer_count=1)

        with mock.patch.object(
            retry_proxy,
            "forward_once",
            side_effect=(warp_response, public_response),
        ) as forward_once:
            response = retry_proxy.resolve(query, PUBLIC_UPSTREAMS)

        self.assertEqual(public_response, response)
        self.assertEqual(PUBLIC_UPSTREAMS[0], forward_once.call_args_list[1].args[0])

    def test_warp_first_falls_back_after_empty_answer(self) -> None:
        query = build_query()
        warp_response = build_response(query, response_code=0)
        public_response = build_response(query, response_code=0, answer_count=1)

        with mock.patch.object(
            retry_proxy,
            "forward_once",
            side_effect=(warp_response, public_response),
        ):
            response = retry_proxy.resolve(query, PUBLIC_UPSTREAMS)

        self.assertEqual(public_response, response)

    def test_warp_first_falls_back_after_servfail_retries(self) -> None:
        query = build_query()
        warp_response = build_response(query, response_code=2)
        public_response = build_response(query, response_code=0, answer_count=1)
        upstream_responses = [warp_response] * retry_proxy.MAXIMUM_WARP_ATTEMPTS + [public_response]

        with (
            mock.patch.object(retry_proxy, "forward_once", side_effect=upstream_responses) as forward_once,
            mock.patch.object(retry_proxy.time, "sleep"),
        ):
            response = retry_proxy.resolve(query, PUBLIC_UPSTREAMS)

        self.assertEqual(public_response, response)
        self.assertEqual(PUBLIC_UPSTREAMS[0], forward_once.call_args_list[-1].args[0])

    def test_warp_first_tries_next_public_upstream_after_timeout(self) -> None:
        query = build_query()
        warp_response = build_response(query, response_code=3)
        public_response = build_response(query, response_code=0, answer_count=1)

        with mock.patch.object(
            retry_proxy,
            "forward_once",
            side_effect=(warp_response, socket.timeout(), public_response),
        ) as forward_once:
            response = retry_proxy.resolve(query, PUBLIC_UPSTREAMS)

        self.assertEqual(public_response, response)
        self.assertEqual(PUBLIC_UPSTREAMS[1], forward_once.call_args_list[-1].args[0])


if __name__ == "__main__":
    unittest.main()
