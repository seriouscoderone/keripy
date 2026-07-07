from keri_serviceaid import Reply


def test_publish_reply_carries_artifact_and_receipt_flag():
    r = Reply.publish(recipient="ERecip", artifact_said="ESchemaSaid",
                      artifact_bytes=b'{"$id":"ESchemaSaid"}',
                      attributes={"schemaSaid": "ESchemaSaid"}, want_receipt=True)
    assert r.kind == "publish"
    assert r.recipient == "ERecip"
    assert r.artifact_said == "ESchemaSaid"
    assert r.artifact_bytes == b'{"$id":"ESchemaSaid"}'
    assert r.attributes == {"schemaSaid": "ESchemaSaid"}
    assert r.want_receipt is True


def test_publish_reply_defaults_want_receipt_false():
    r = Reply.publish(recipient="ERecip", artifact_said="EX",
                      artifact_bytes=b"{}", attributes={})
    assert r.want_receipt is False
