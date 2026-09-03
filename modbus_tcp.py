try:
    import usocket as socket
except ImportError:  # CPython tests
    import socket

try:
    import ustruct as struct
except ImportError:  # CPython tests
    import struct


class ModbusError(OSError):
    pass


def _uint32_words(value):
    value = int(value) & 0xFFFFFFFF
    return ((value >> 16) & 0xFFFF, value & 0xFFFF)


class ModbusTcpClient:
    def __init__(self, host, port=502, unit_id=1, timeout=5):
        self.host = host
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.timeout = timeout
        self.sock = None
        self.transaction = 0
        self.last_limits = None
        self.last_phase = "idle"

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        # A reconnect must re-assert the limits even if their values did not change.
        self.last_limits = None

    def _connect(self):
        if self.sock is not None:
            return
        self.last_phase = "dns"
        address = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)[0][-1]
        sock = socket.socket()
        sock.settimeout(self.timeout)
        self.last_phase = "tcp-connect"
        sock.connect(address)
        self.sock = sock

    def _recv_exact(self, length):
        chunks = bytearray()
        while len(chunks) < length:
            part = self.sock.recv(length - len(chunks))
            if not part:
                raise ModbusError("connection closed")
            chunks.extend(part)
        return bytes(chunks)

    def _request(self, function, payload):
        self.transaction = (self.transaction + 1) & 0xFFFF
        pdu = bytes((function,)) + payload
        frame = struct.pack(">HHHB", self.transaction, 0, len(pdu) + 1, self.unit_id) + pdu
        try:
            self._connect()
            self.last_phase = "modbus-send-function-%d" % function
            self.sock.sendall(frame)
            self.last_phase = "modbus-read-header-function-%d" % function
            header = self._recv_exact(7)
            transaction, protocol, length, unit = struct.unpack(">HHHB", header)
            if transaction != self.transaction or protocol != 0 or unit != self.unit_id:
                raise ModbusError("invalid Modbus response header")
            if length < 2 or length > 260:
                raise ModbusError("invalid Modbus response length")
            self.last_phase = "modbus-read-body-function-%d" % function
            response = self._recv_exact(length - 1)
            if not response:
                raise ModbusError("empty Modbus response")
            if response[0] == (function | 0x80):
                raise ModbusError("Modbus exception %d" % response[1])
            if response[0] != function:
                raise ModbusError("unexpected Modbus function")
            self.last_phase = "complete"
            return response[1:]
        except Exception:
            self.close()
            raise

    def write_register(self, address, value):
        payload = struct.pack(">HH", int(address), int(value) & 0xFFFF)
        response = self._request(6, payload)
        if response != payload:
            raise ModbusError("write-register echo mismatch")

    def write_registers(self, address, values):
        values = tuple(int(value) & 0xFFFF for value in values)
        raw = b"".join(struct.pack(">H", value) for value in values)
        payload = struct.pack(">HHB", int(address), len(values), len(raw)) + raw
        response = self._request(16, payload)
        if response != struct.pack(">HH", int(address), len(values)):
            raise ModbusError("write-registers echo mismatch")

    def read_input_registers(self, address, count):
        response = self._request(4, struct.pack(">HH", int(address), int(count)))
        if not response or response[0] != count * 2 or len(response) != count * 2 + 1:
            raise ModbusError("invalid input-register response")
        return [struct.unpack(">H", response[1 + i * 2:3 + i * 2])[0]
                for i in range(count)]

    def push_limits(self, amps, offline_amps, connector_id=0):
        online_current = int(max(0, amps) * 100)
        offline_current = int(max(0, offline_amps) * 100)
        values = (int(max(0, amps) * 230), online_current,
                  int(max(0, offline_amps) * 230), offline_current)
        if values == self.last_limits:
            return
        base = 20000 + int(connector_id) * 1000
        self.write_register(0, 1)
        words = (_uint32_words(values[0]) + _uint32_words(values[1]) +
                 _uint32_words(values[2]) + _uint32_words(values[3]))
        self.write_registers(base, words)
        self.last_limits = values

    def read_status(self, connector_id=0):
        words = self.read_input_registers(10000 + int(connector_id) * 1000, 23)
        def u32(offset):
            return ((words[offset] & 0xFFFF) << 16) | (words[offset + 1] & 0xFFFF)
        return {
            "state": words[0],
            "voltage_v": u32(9) / 100,
            "current_amps": u32(15) / 100,
            "power_w": u32(21),
        }
