"""Create the server stubs whose replies are ENCRYPTED (they need the game's secret).

Reads secret.txt (written by decrypt_cache.py). Writes into stubs/:
  AuthenticationWebService/LoginGuest.xml    guest login
  AuthenticationWebService/LoginParent.xml   parent login, with one child profile
  AuthenticationWebService/LoginChild.xml    child login (the session token as an encrypted string)
  RegistrationWebService/RegisterParent.xml  account registration (also used for guest registration)
The game decrypts these as: base64 -> Triple DES ECB, key = MD5(UTF-16LE(secret)) -> UTF-16 text,
inside a plain <string> element. Every other reply is made by gen_default_stubs.py.

Usage:  python make_stubs.py
"""
import base64, hashlib, os, sys

try:
    from Crypto.Cipher import DES3
    def tdes_ecb_encrypt(key16, data): return DES3.new(key16, DES3.MODE_ECB).encrypt(data)
except ImportError:
    try:
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        except ImportError:
            from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        def tdes_ecb_encrypt(key16, data):
            e = Cipher(TripleDES(key16 + key16[:8]), modes.ECB()).encryptor()
            return e.update(data) + e.finalize()
    except ImportError:
        sys.exit("Install one of:  pip install pycryptodome   |   pip install cryptography")

HERE = os.path.dirname(os.path.abspath(__file__))
STUBS = os.path.join(HERE, "stubs")
TOKEN = "5a3f0e2c-8d41-4c5b-9a77-1e2d3c4b5a69"
USER_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
NS = 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"'

def encrypt_unicode(secret, text):
    key = hashlib.md5(secret.encode("utf-16-le")).digest()
    pt = text.encode("utf-16-le")
    pad = 8 - len(pt) % 8
    return base64.b64encode(tdes_ecb_encrypt(key, pt + bytes([pad]) * pad)).decode()

def write(rel, text):
    path = os.path.join(STUBS, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("wrote", os.path.relpath(path, HERE))

def wrapped(secret, text):
    return '<?xml version="1.0" encoding="utf-8"?>\n<string>' + encrypt_unicode(secret, text) + '</string>\n'

def main():
    sp = os.path.join(HERE, "secret.txt")
    if not os.path.exists(sp): sys.exit("secret.txt not found - run decrypt_cache.py first")
    secret = open(sp, encoding="utf-8").read().strip()

    person = f'''  <UserName>Player</UserName>
  <FirstName>Player</FirstName>
  <LastName />
  <Email />
  <ApiToken>{TOKEN}</ApiToken>
  <UserID>{USER_ID}</UserID>'''
    guest = f'''<?xml version="1.0" encoding="utf-16"?>
<ParentLoginInfo {NS}>
{person}
  <SendActivationReminder>false</SendActivationReminder>
  <UnAuthorized>false</UnAuthorized>
</ParentLoginInfo>'''
    write("AuthenticationWebService/LoginGuest.xml", wrapped(secret, guest))

    parent = f'''<?xml version="1.0" encoding="utf-16"?>
<ParentLoginInfo {NS}>
{person}
  <LoginStatus>Success</LoginStatus>
  <ChildList>
{person}
  </ChildList>
  <SendActivationReminder>false</SendActivationReminder>
  <UnAuthorized>false</UnAuthorized>
</ParentLoginInfo>'''
    write("AuthenticationWebService/LoginParent.xml", wrapped(secret, parent))

    write("AuthenticationWebService/LoginChild.xml", wrapped(secret, TOKEN))

    reg = f'''<?xml version="1.0" encoding="utf-16"?>
<RegistrationResult {NS}>
  <Status>Success</Status>
  <ApiToken>{TOKEN}</ApiToken>
  <UserID>{USER_ID}</UserID>
</RegistrationResult>'''
    write("RegistrationWebService/RegisterParent.xml", wrapped(secret, reg))
    print("done. Now run gen_default_stubs.py for all the other replies.")

if __name__ == "__main__":
    main()
