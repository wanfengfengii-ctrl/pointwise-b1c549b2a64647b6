import unittest

from station.capture.pages import PageStore


class TestPages(unittest.TestCase):
    def test_overwrite_one_physical_address(self):
        pages = PageStore(2)
        self.addCleanup(pages.close)
        pages.write_page(0, b'\x00\x02')
        original_read = pages.read_page(0)
        pages.write_page(4, b'\x01\x04')
        pages.write_page(0, b'\x03\x04')
        self.assertEqual(pages.page_count, 2)
        self.assertEqual(pages.read_page(0), b'\x03\x04')
        self.assertEqual(pages.read_page(4), b'\x01\x04')
        self.assertEqual(original_read, b'\x00\x02')
        with self.assertRaises(KeyError):
            pages.read_page(1)

    def test_write_checks_before_changing_page(self):
        pages = PageStore(2)
        self.addCleanup(pages.close)
        pages.write_page(1, b'\x03\x04')
        for address, data in ((1, b'\x03'), (1, bytearray(2)), (-1, b'\x01\x02'), (True, b'\x01\x02')):
            with self.subTest(address=address, data=data), self.assertRaises(ValueError):
                pages.write_page(address, data)
        self.assertEqual(pages.read_page(1), b'\x03\x04')
        self.assertEqual(pages.page_count, 1)
        pages.close()
        with self.assertRaises(RuntimeError):
            pages.read_page(1)
        with self.assertRaises(RuntimeError):
            pages.write_page(1, b'\x05\x06')
