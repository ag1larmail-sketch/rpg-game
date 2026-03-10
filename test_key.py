import os
from dotenv import load_dotenv

load_dotenv()
key = os.getenv('DEEPSEEK_API_KEY')
print(f"Ключ: {key[:10]}... (первые 10 символов)")
print(f"Длина ключа: {len(key) if key else 0} символов")