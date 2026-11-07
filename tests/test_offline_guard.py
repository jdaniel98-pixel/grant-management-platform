"""The suite cannot reach the internet or use a real OpenAI key."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import socket
import unittest

from grant_platform.services import ai


class OfflineGuardTests(unittest.TestCase):
    def test_no_openai_setting_survives_into_the_tests(self):
        self.assertFalse([name for name in os.environ if name.startswith("OPENAI_")])
        self.assertFalse(ai.configured())

    def test_connections_outside_this_computer_are_refused(self):
        with self.assertRaises(offline_guard.ExternalNetworkBlocked):
            socket.create_connection(("api.openai.com", 443), timeout=1)
        with self.assertRaises(offline_guard.ExternalNetworkBlocked):
            socket.getaddrinfo("example.org", 443)
        with socket.socket() as sock, self.assertRaises(offline_guard.ExternalNetworkBlocked):
            sock.connect(("203.0.113.9", 80))

    def test_this_computer_is_still_reachable(self):
        self.assertTrue(socket.getaddrinfo("localhost", 80))


if __name__ == "__main__":
    unittest.main()
