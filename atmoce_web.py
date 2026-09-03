try:
    import usocket as socket
except ImportError:
    import socket
try:
    import ussl as ssl
except ImportError:
    import ssl
try:
    import ujson as json
except ImportError:
    import json
try:
    import ubinascii as binascii
except ImportError:
    import binascii

HOST = "www.atmocecloud.com"
LOGIN_PATH = "/permission-auth/api/login"
DETAIL_PATH = "/energy-manage/stationStatisticalData/getSingleStationsDetailData"
MAX_RESPONSE_BYTES = 8192


def encode_password(password):
    """Match the Atmoce web request: Base64 password with encrypted=true."""
    return binascii.b2a_base64(str(password).encode()).strip().decode()


def _dechunk(body):
    out = bytearray()
    offset = 0
    while True:
        line_end = body.find(b"\r\n", offset)
        if line_end < 0:
            raise OSError("invalid chunked Atmoce response")
        size = int(body[offset:line_end].split(b";", 1)[0], 16)
        if size == 0:
            return bytes(out)
        offset = line_end + 2
        end = offset + size
        if end + 2 > len(body) or body[end:end + 2] != b"\r\n":
            raise OSError("short chunked Atmoce response")
        out.extend(body[offset:end])
        offset = end + 2


def _session_from_headers(headers):
    for line in headers.split(b"\r\n"):
        if line.lower().startswith(b"set-cookie:"):
            cookie = line.split(b":", 1)[1].strip().split(b";", 1)[0]
            if cookie.upper().startswith(b"SESSION="):
                return cookie.split(b"=", 1)[1].decode()
    return ""


def _needs_login(payload, status=200):
    if status in (401, 403):
        return True
    if payload.get("success"):
        return False
    if payload.get("code") in (401, 403):
        return True
    message = str(payload.get("msg") or "").lower()
    return "login" in message or "expired" in message or "unauthorized" in message


def snapshot_from_payload(payload, voltage):
    if not payload.get("success") or not isinstance(payload.get("data"), dict):
        raise OSError(str(payload.get("msg") or "Atmoce response failed"))
    data = payload["data"]
    grid_w = float(data.get("gridPower") or 0)
    storage_w = float(data.get("storagePower") or 0)
    pv_w = float(data.get("generationPower") or 0)
    voltage = float(voltage)
    return {
        "grid_amps": (grid_w + storage_w) / voltage,
        "grid_raw_amps": grid_w / voltage,
        "grid_raw_power_w": grid_w,
        "storage_amps": storage_w / voltage,
        "grid_power_w": grid_w + storage_w,
        "storage_power_w": storage_w,
        "pv_power_w": pv_w,
        "battery_soc": float(data.get("storageSoe") or 0),
        "online": data.get("online"),
        "data_time": data.get("dataTime"),
    }


class AtmoceWebClient:
    def __init__(self, station_id, token="", username="", password="",
                 session="", voltage=230, timeout=12, password_encoded=False):
        self.station_id = int(station_id)
        self.token = self._raw_token(token)
        self.username = str(username).strip()
        self.password = str(password)
        self.password_encoded = bool(password_encoded)
        self.session = self._raw_session(session)
        self.voltage = float(voltage)
        self.timeout = timeout
        self.last_phase = "idle"

    @staticmethod
    def _raw_token(value):
        text = str(value).strip()
        return text[7:].strip() if text.lower().startswith("bearer ") else text

    @staticmethod
    def _raw_session(value):
        text = str(value).strip()
        return text[8:].strip() if text.upper().startswith("SESSION=") else text

    @staticmethod
    def _safe_header(value):
        text = str(value)
        if "\r" in text or "\n" in text:
            raise ValueError("invalid Atmoce HTTP header value")
        return text

    def _read_response(self, stream):
        raw = bytearray()
        while len(raw) < MAX_RESPONSE_BYTES:
            part = stream.read(min(1024, MAX_RESPONSE_BYTES - len(raw)))
            if not part:
                break
            raw.extend(part)
        if len(raw) >= MAX_RESPONSE_BYTES:
            raise OSError("Atmoce response too large")
        header_end = raw.find(b"\r\n\r\n")
        line_end = raw.find(b"\r\n")
        if header_end < 0 or line_end < 0:
            raise OSError("invalid Atmoce HTTP response")
        status_parts = bytes(raw[:line_end]).split()
        if len(status_parts) < 2:
            raise OSError("invalid Atmoce HTTP status")
        status = int(status_parts[1])
        headers = bytes(raw[line_end + 2:header_end])
        body = bytes(raw[header_end + 4:])
        if b"transfer-encoding: chunked" in headers.lower():
            body = _dechunk(body)
        try:
            payload = json.loads(body)
        except ValueError:
            if status in (401, 403):
                payload = {}
            else:
                raise OSError("Atmoce HTTP %d returned invalid JSON" % status)
        return payload, status, _session_from_headers(headers)

    def _post_json(self, path, value, authorization=""):
        self.last_phase = "build-request"
        body = json.dumps(value).encode()
        headers = ["POST %s HTTP/1.0" % path, "Host: %s" % HOST,
                   "Content-Type: application/json", "Accept: application/json"]
        if authorization:
            headers.append("Authorization: " + self._safe_header(authorization))
        if self.session:
            headers.append("Cookie: SESSION=" + self._safe_header(self.session))
        headers.extend(("Content-Length: %d" % len(body), "Connection: close", "", ""))
        request = "\r\n".join(headers).encode() + body
        self.last_phase = "dns"
        address = socket.getaddrinfo(HOST, 443, 0, socket.SOCK_STREAM)[0][-1]
        sock = socket.socket()
        sock.settimeout(self.timeout)
        wrapped = None
        try:
            self.last_phase = "tcp-connect"
            sock.connect(address)
            self.last_phase = "tls-handshake"
            try:
                wrapped = ssl.wrap_socket(sock, server_hostname=HOST)
            except TypeError:
                wrapped = ssl.wrap_socket(sock)
            self.last_phase = "http-write"
            offset = 0
            while offset < len(request):
                written = wrapped.write(request[offset:])
                if not written:
                    raise OSError("Atmoce TLS write failed")
                offset += written
            self.last_phase = "http-read"
            payload, status, session = self._read_response(wrapped)
            if session:
                self.session = session
            self.last_phase = "complete"
            return payload, status
        finally:
            try:
                (wrapped if wrapped is not None else sock).close()
            except OSError:
                pass

    def login(self):
        if not self.username or not self.password:
            raise ValueError("Atmoce username and password are required to log in")
        request_body = {
            "username": self.username,
            "encrypted": True,
            "password": self.password if self.password_encoded else encode_password(self.password),
            "appType": "web",
        }
        previous_session = self.session
        payload, status = self._post_json(LOGIN_PATH, request_body)
        data = payload.get("data")
        token = data.get("token") if isinstance(data, dict) else None
        if not token and self.session and self.session != previous_session:
            payload, status = self._post_json(LOGIN_PATH, request_body)
            data = payload.get("data")
            token = data.get("token") if isinstance(data, dict) else None
        if status != 200 or not token:
            raise PermissionError(str(payload.get("msg") or "Atmoce login failed"))
        self.token = self._raw_token(token)
        return self.token

    def _detail(self):
        if not self.token:
            self.login()
        return self._post_json(DETAIL_PATH, {"stationId": self.station_id},
                               "Bearer " + self._safe_header(self.token))

    def read(self):
        if self.station_id <= 0:
            raise ValueError("Atmoce station id is required")
        payload, status = self._detail()
        if _needs_login(payload, status):
            self.login()
            payload, status = self._detail()
        if status != 200:
            raise OSError("Atmoce HTTP %d" % status)
        return snapshot_from_payload(payload, self.voltage)
