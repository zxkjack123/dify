import base64
import binascii
import hashlib

def hash_password(password_str, salt_byte):
    dk = hashlib.pbkdf2_hmac("sha256", password_str.encode("utf-8"), salt_byte, 10000)
    return binascii.hexlify(dk)

password = "Z4_1difpwd"
salt_base64 = "fBxRW6bWBd0EurbTqsEdAw=="
salt_byte = base64.b64decode(salt_base64)

new_hash_bytes = hash_password(password, salt_byte)
new_hash_base64 = base64.b64encode(new_hash_bytes).decode()

print(f"New Hash: {new_hash_base64}")
print(f"SQL: UPDATE accounts SET password = '{new_hash_base64}' WHERE email = 'zxkjack123@163.com';")
