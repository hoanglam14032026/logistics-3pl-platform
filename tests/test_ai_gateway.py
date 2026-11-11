from app.ai_gateway import ask_logistics

def test_ask_logistics_khi_llama_tat_khong_crash():
    result = ask_logistics("test")
    assert isinstance(result, str)
    assert len(result) > 0