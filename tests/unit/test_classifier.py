from webarchiver.extract.classifier import classify, subfolder_for
from webarchiver.extract.models import AssetType


def test_classify_by_extension():
    assert classify("https://a.com/x.png") is AssetType.IMAGE
    assert classify("https://a.com/x.mp4") is AssetType.VIDEO
    assert classify("https://a.com/x.pdf") is AssetType.DOCUMENT
    assert classify("https://a.com/x.css") is AssetType.CSS
    assert classify("https://a.com/x.woff2") is AssetType.FONT


def test_subfolder_routing():
    assert subfolder_for(AssetType.IMAGE, "https://a.com/x.jpeg") == "images/jpg"
    assert subfolder_for(AssetType.IMAGE, "https://a.com/x.png") == "images/png"
    assert subfolder_for(AssetType.DOCUMENT, "https://a.com/x.pdf") == "pdf"
    assert subfolder_for(AssetType.DOCUMENT, "https://a.com/x.docx") == "documents"
