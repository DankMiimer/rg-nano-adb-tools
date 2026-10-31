#!/usr/bin/env python3
"""Offline tests for wifi/app/wifi_backend.py: no Nano needed.

    python3 tests/test_wifi_backend.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "wifi", "app"))
DATA = tempfile.mkdtemp(prefix="wifi_backend_test_")
os.environ["WIFI_DATA"] = DATA
os.environ["WIFI_RUN"] = "/tmp/wifi"
import wifi_backend as be  # noqa: E402


class Ssid(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(be.decode_ssid("Home Net"), b"Home Net")

    def test_escapes(self):
        # wpa_cli's printf_encode: \\ \" \e \n \r \t and \xNN for the rest
        self.assertEqual(be.decode_ssid(r'a\\b\"c\x00\xffd\te'), b'a\\b"c\x00\xffd\te')

    def test_utf8(self):
        self.assertEqual(be.decode_ssid("Blåbær").decode(), "Blåbær")


class Security(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(be.security("[WPA2-PSK-CCMP][ESS]"), "psk")
        self.assertEqual(be.security("[WPA2-PSK+SAE-CCMP][ESS]"), "psk")   # WPA2/WPA3 mixed
        self.assertEqual(be.security("[RSN-SAE-CCMP][ESS]"), "other")      # WPA3 only
        self.assertEqual(be.security("[WPA2-EAP-CCMP][ESS]"), "other")
        self.assertEqual(be.security("[WEP][ESS]"), "other")
        self.assertEqual(be.security("[ESS]"), "open")


class Psk(unittest.TestCase):
    def test_ieee_vector(self):
        # IEEE 802.11i-2004 H.4.1 test vector
        self.assertEqual(be.psk_for(b"IEEE", "password"),
                         "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e")

    def test_raw_hex_key_kept(self):
        k = "AB" * 32
        self.assertEqual(be.psk_for(b"x", k), k.lower())


class Conf(unittest.TestCase):
    def setUp(self):
        if os.path.exists(be.CONF):
            os.remove(be.CONF)

    def blocks(self):
        return be.read_conf()[1]

    def test_new_file_has_header(self):
        be.save_network(b"Home", "secret123")
        header, blocks = be.read_conf()
        self.assertIn("update_config=1", header)
        self.assertIn("ctrl_interface=/tmp/wifi/ctrl", header)
        self.assertEqual(blocks[0][0], "ssid=" + b"Home".hex())

    def test_no_password_stored(self):
        be.save_network(b"Home", "secret123")
        with open(be.CONF) as f:
            text = f.read()
        self.assertNotIn("secret123", text)
        self.assertIn("psk=" + be.psk_for(b"Home", "secret123"), text)

    def test_open_network(self):
        be.save_network(b"Cafe", "")
        self.assertIn("key_mgmt=NONE", self.blocks()[0])

    def test_newest_first_with_highest_priority(self):
        be.save_network(b"A", "aaaaaaaa")
        be.save_network(b"B", "bbbbbbbb")
        b = self.blocks()
        self.assertEqual([be.block_ssid(x) for x in b], [b"B", b"A"])
        self.assertIn("priority=2", b[0])

    def test_replace_same_ssid(self):
        be.save_network(b"A", "aaaaaaaa")
        be.save_network(b"A", "newpassw")
        b = self.blocks()
        self.assertEqual(len(b), 1)
        self.assertIn("psk=" + be.psk_for(b"A", "newpassw"), b[0])

    def test_keeps_networks_written_by_wpa_supplicant(self):
        # quoted SSIDs and other keys, as wpa_supplicant's save_config writes them
        with open(be.CONF, "w") as f:
            f.write('ctrl_interface=/tmp/wifi/ctrl\nupdate_config=1\n\nnetwork={\n'
                    '\tssid="Old"\n\tpsk=0123\n\tdisabled=1\n}\n')
        be.save_network(b"New", "nnnnnnnn")
        b = self.blocks()
        self.assertEqual([be.block_ssid(x) for x in b], [b"New", b"Old"])
        self.assertIn("disabled=1", b[1])
        be.forget_network(b"Old")
        self.assertEqual([be.block_ssid(x) for x in self.blocks()], [b"New"])

    def test_odd_ssid_round_trip(self):
        odd = b'quote" back\\ \x01'
        be.save_network(odd, "pppppppp")
        self.assertEqual(be.block_ssid(self.blocks()[0]), odd)


if __name__ == "__main__":
    unittest.main(verbosity=1)
