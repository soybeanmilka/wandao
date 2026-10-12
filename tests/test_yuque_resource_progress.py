import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.yuque.backend.export_yuque import localize_resources, normalize_resources


class YuqueResourceProgressTests(unittest.TestCase):
    def test_reports_each_downloadable_resource_in_order_and_keeps_going_after_failure(self) -> None:
        resources = [
            {"url": "https://cdn.example.com/one.png", "kind": "image", "title": "one"},
            {"url": "https://cdn.example.com/file.pdf", "kind": "attachment", "title": "file"},
            {"url": "https://cdn.example.com/two.png", "kind": "image", "title": "two"},
        ]
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md_path = root / "doc.md"
            with patch(
                "plugins.yuque.backend.export_yuque.download_resource",
                side_effect=[root / "assets" / "one.png", RuntimeError("download failed"), root / "assets" / "two.png"],
            ):
                _markdown, success, failures = localize_resources(
                    "https://cdn.example.com/one.png https://cdn.example.com/file.pdf https://cdn.example.com/two.png",
                    resources,
                    md_path,
                    timeout=30,
                    keep_remote=True,
                    cookies=[],
                    download_attachments=True,
                    progress_callback=events.append,
                )

        self.assertEqual(success, {"image": 2, "attachment": 0})
        self.assertEqual(len(failures), 1)
        self.assertEqual(
            [(event["status"], event["index"], event["total"], event["kind"]) for event in events],
            [
                ("started", 1, 3, "image"),
                ("succeeded", 1, 3, "image"),
                ("started", 2, 3, "attachment"),
                ("failed", 2, 3, "attachment"),
                ("started", 3, 3, "image"),
                ("succeeded", 3, 3, "image"),
            ],
        )
        self.assertEqual(events[3]["error"], "download failed")

    def test_skipped_attachments_do_not_count_toward_progress_total(self) -> None:
        resources = [
            {"url": "https://cdn.example.com/one.png", "kind": "image", "title": "one"},
            {"url": "https://cdn.example.com/file.pdf", "kind": "attachment", "title": "file"},
        ]
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("plugins.yuque.backend.export_yuque.download_resource", return_value=root / "assets" / "one.png"):
                localize_resources(
                    "https://cdn.example.com/one.png https://cdn.example.com/file.pdf",
                    resources,
                    root / "doc.md",
                    timeout=30,
                    keep_remote=True,
                    cookies=[],
                    download_attachments=False,
                    progress_callback=events.append,
                )

        self.assertEqual([(event["index"], event["total"], event["kind"]) for event in events], [(1, 1, "image"), (1, 1, "image")])

    def test_progress_and_failures_redact_signed_resource_urls(self) -> None:
        signed_url = "https://cdn.example.com/file.png?X-Amz-Signature=secret&X-Amz-Credential=token"
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "plugins.yuque.backend.export_yuque.download_resource",
                side_effect=RuntimeError(f"request failed: {signed_url}"),
            ):
                _markdown, _success, failures = localize_resources(
                    signed_url,
                    [{"url": signed_url, "kind": "image", "title": "private"}],
                    root / "doc.md",
                    timeout=30,
                    keep_remote=True,
                    cookies=[],
                    download_attachments=True,
                    progress_callback=events.append,
                )

        serialized = repr([*events, *failures])
        self.assertNotIn("X-Amz-Signature", serialized)
        self.assertNotIn("secret", serialized)
        self.assertIn("https://cdn.example.com/file.png", serialized)

    def test_normalize_resources_upgrades_http_urls(self) -> None:
        normalized = normalize_resources(
            [
                {"url": "http://cdn.example.com/a.doc", "kind": "attachment", "title": "a"},
                {"url": "http://cdn.example.com/b.png", "kind": "image", "title": "b"},
            ],
            ["http://cdn.example.com/c.png"],
        )
        self.assertEqual(
            [item["url"] for item in normalized],
            [
                "https://cdn.example.com/a.doc",
                "https://cdn.example.com/b.png",
                "https://cdn.example.com/c.png",
            ],
        )
        self.assertTrue(all(item["url"].startswith("https://") for item in normalized))

    def test_localize_resources_replaces_http_form_in_markdown(self) -> None:
        markdown = "[合同](http://cdn.example.com/contract.doc)"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "attachments" / "contract.doc"
            target.parent.mkdir()
            target.write_bytes(b"doc")
            with patch("plugins.yuque.backend.export_yuque.download_resource", return_value=target):
                updated, success, failures = localize_resources(
                    markdown,
                    [{"url": "https://cdn.example.com/contract.doc", "kind": "attachment", "title": "合同"}],
                    root / "doc.md",
                    timeout=30,
                    keep_remote=True,
                    cookies=[],
                    download_attachments=True,
                )
        self.assertEqual(success, {"image": 0, "attachment": 1})
        self.assertEqual(failures, [])
        self.assertNotIn("http://cdn.example.com/contract.doc", updated)
        self.assertIn("attachments/contract.doc", updated)


if __name__ == "__main__":
    unittest.main()
