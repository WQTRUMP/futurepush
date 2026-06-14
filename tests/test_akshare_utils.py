from futures_signal.akshare_utils import brief_error, first_float, first_int, infer_contract, infer_product, rows


def test_rows_and_error_helpers_cover_remaining_branches():
    class Frame:
        def to_dict(self, orient):
            assert orient == "records"
            return [{"a": 1}]

    assert rows(Frame()) == [{"a": 1}]
    assert rows("bad") == []

    message = brief_error(RuntimeError("x" * 300), limit=20)
    assert message.startswith("RuntimeError: ")
    assert message.endswith("...")


def test_infer_helpers_cover_named_products_and_missing_values():
    assert infer_product({"name": "上证50"}) == "IH"
    assert infer_product({"name": "中证500"}) == "IC"
    assert infer_product({"name": "中证1000"}) == "IM"
    assert infer_product({"name": "unknown"}) is None
    assert infer_contract({"name": "plain text"}) is None


def test_first_number_helpers_cover_present_and_missing_keys():
    assert first_float({"price": "12.5"}, ["price"], None) == 12.5
    assert first_float({}, ["price"], 1.0) == 1.0
    assert first_int({"volume": "12"}, ["volume"], None) == 12
    assert first_int({}, ["volume"], 3) == 3
