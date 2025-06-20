"""Verify the PBX registrar's owned local softphone account with real SIP packets."""
import hashlib
import json
import re
import secrets
import socket
import uuid


def md5(value):
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


def registration(phone, password, expires):
    port = phone.getsockname()[1]
    call_id, tag, authorization = str(uuid.uuid4()), secrets.token_hex(8), ""
    for sequence in (1, 2):
        uri = "sip:127.0.0.1:18160"
        message = "\r\n".join([
            f"REGISTER {uri} SIP/2.0",
            f"Via: SIP/2.0/UDP 127.0.0.1:{port};branch=z9hG4bK{secrets.token_hex(8)};rport",
            "Max-Forwards: 70", f"From: <sip:1002@127.0.0.1>;tag={tag}",
            "To: <sip:1002@127.0.0.1>", f"Call-ID: {call_id}", f"CSeq: {sequence} REGISTER",
            f"Contact: <sip:1002@127.0.0.1:{port}>", f"Expires: {expires}",
            *([authorization] if authorization else []), "Content-Length: 0", "", ""])
        phone.sendto(message.encode(), ("127.0.0.1", 18160))
        response = phone.recv(8192).decode("ascii")
        status = int(response.split()[1])
        if status != 401 or sequence == 2:
            return status, call_id, response
        challenge = next(line.split(":", 1)[1].strip() for line in response.splitlines()
                         if line.lower().startswith("www-authenticate:"))
        values = dict((key.lower(), quoted or bare) for key, quoted, bare in
                      re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', challenge))
        if values.get("algorithm", "MD5").upper() != "MD5" or "auth" not in values.get("qop", "").split(","):
            raise RuntimeError("Unexpected local fixture digest challenge")
        nonce, realm, cnonce = values["nonce"], values["realm"], secrets.token_hex(12)
        digest = md5(f"{md5('1002:'+realm+':'+password)}:{nonce}:00000001:{cnonce}:auth:{md5('REGISTER:'+uri)}")
        authorization = (f'Authorization: Digest username="1002", realm="{realm}", nonce="{nonce}", '
                         f'uri="{uri}", response="{digest}", algorithm=MD5, qop=auth, '
                         f'nc=00000001, cnonce="{cnonce}"')
        if "opaque" in values:
            authorization += f', opaque="{values["opaque"]}"'
    raise RuntimeError("Registration challenge did not complete")

if __name__ == "__main__":
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as phone:
        phone.bind(("127.0.0.1", 0))
        phone.settimeout(3)
        rejected, _, _ = registration(phone, "deliberately-wrong-fixture-password", 60)
        assert rejected == 401, rejected
        accepted, call_id, _ = registration(phone, "local-operator-pass", 60)
        assert accepted == 200, accepted
        removed, _, response = registration(phone, "local-operator-pass", 0)
        assert removed == 200 and "\nContact:" not in response, response
        print(json.dumps({"wrong_password": rejected, "registered": accepted, "removed": removed,
                          "dialog_id": call_id, "registrar": "asterisk", "account": "1002"}))
