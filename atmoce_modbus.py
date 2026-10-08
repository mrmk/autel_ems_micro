"""Memory-small Atmoce MC100L/MC100/MG100 Modbus TCP meter reader."""

from modbus_tcp import ModbusTcpClient, int32, uint32


REG_PV_POWER = 60069
REG_BATTERY_POWER = 60071
REG_GRID_POWER = 60073
REG_GRID_VOLTAGE = 60089
REG_BATTERY_SOC = 60095


class AtmoceModbusClient:
    def __init__(self, host, port=502, unit_id=1, timeout=5):
        self.host = str(host).strip()
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.client = ModbusTcpClient(
            self.host, self.port, self.unit_id, timeout=timeout)

    @property
    def last_phase(self):
        return self.client.last_phase

    @property
    def endpoint(self):
        return "%s:%d" % (self.host, self.port)

    def close(self):
        self.client.close()

    def read(self):
        if not self.host:
            raise ValueError("Atmoce Modbus host is required")

        pv_power_w = float(uint32(
            self.client.read_holding_registers(REG_PV_POWER, 2)))
        # Keep Atmoce's Web/API sign convention: negative is charging and
        # positive is discharging.
        storage_power_w = float(int32(
            self.client.read_holding_registers(REG_BATTERY_POWER, 2)))
        grid_raw_power_w = float(int32(
            self.client.read_holding_registers(REG_GRID_POWER, 2)))
        grid_voltage_v = (
            self.client.read_holding_registers(REG_GRID_VOLTAGE, 1)[0] * 0.1)
        battery_soc = float(
            self.client.read_holding_registers(REG_BATTERY_SOC, 1)[0])
        storage_amps = (storage_power_w / grid_voltage_v
                        if grid_voltage_v else 0.0)
        grid_raw_amps = (grid_raw_power_w / grid_voltage_v
                         if grid_voltage_v else 0.0)
        grid_power_w = grid_raw_power_w + storage_power_w
        grid_amps = (grid_power_w / grid_voltage_v
                     if grid_voltage_v else 0.0)

        return {
            "provider": "atmoce_modbus",
            "source": "Atmoce Modbus",
            "endpoint": self.endpoint,
            "grid_amps": grid_amps,
            "grid_raw_amps": grid_raw_amps,
            "grid_power_w": grid_power_w,
            "grid_raw_power_w": grid_raw_power_w,
            "grid_voltage_v": grid_voltage_v,
            "storage_amps": storage_amps,
            "storage_power_w": storage_power_w,
            "pv_power_w": pv_power_w,
            "battery_soc": battery_soc,
        }
