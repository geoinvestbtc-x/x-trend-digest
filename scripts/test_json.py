import json

raw = '                                                                                                                                                                                                                                                                                                                                                                                                                '
try:
    data = json.loads(raw)
except Exception as e:
    print(f"Error: {e}")
