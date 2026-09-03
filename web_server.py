try:
    import uasyncio as asyncio
except ImportError:  # CPython tests
    import asyncio

try:
    import ujson as json
except ImportError:  # CPython tests
    import json

import config_store


MAX_HEADER = 4096
MAX_BODY = 4096

HTML = b"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Autel EMS Micro</title><style>
body{font:15px system-ui;margin:0;background:#10151d;color:#e9f0f8}main{max-width:760px;margin:auto;padding:18px}
h1{font-size:24px;margin:4px 0 18px}h2{font-size:18px;margin:0 0 16px}.card{background:#18212c;border:1px solid #2d3b4b;border-radius:12px;padding:18px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-top:14px}.grid>div{min-width:0}label{display:block;color:#aebdca}
input,select,button{box-sizing:border-box;width:100%;padding:10px;margin-top:5px;border-radius:8px;border:1px solid #405166;background:#0f1720;color:#fff}
input[type=checkbox]{width:auto;margin-right:8px}
button{background:#16a269;border:0;font-weight:700;cursor:pointer}.metric{font-size:22px}.muted{color:#91a1b1}.bad{color:#ff8585}.good{color:#69dfa8}
.statusbar{display:flex;flex-wrap:wrap;gap:8px;padding-bottom:14px;border-bottom:1px solid #2d3b4b}.pill{display:inline-block;padding:5px 10px;border-radius:99px;background:#293544}.pill.bad{background:#493035}.pill.good{background:#173f32}.details{margin-top:16px;padding-top:12px;border-top:1px solid #2d3b4b;font-size:13px;line-height:1.7}.error{margin:16px 0 0;padding:10px 12px;border-radius:8px;background:#493035}.error:empty{display:none}
</style></head><body><main><h1>Autel EMS Micro</h1>
<section class=card><h2>System status</h2><div class=statusbar id=health><span class="pill muted" id=sWifi>Loading Wi-Fi...</span><span class="pill muted" id=sMeter>Loading meter...</span><span class="pill muted" id=sControl>Loading controller...</span></div><div class=grid>
<div><span class=muted>Mode</span><div class=metric id=sMode>-</div></div><div><span class=muted>EV limit</span><div class=metric id=sLimit>-</div></div>
<div><span class=muted>Grid (raw)</span><div class=metric id=sGrid>-</div></div><div><span class=muted>Battery</span><div class=metric id=sBattery>-</div></div>
<div><span class=muted>Charger</span><div class=metric id=sCharger>-</div></div><div><span class=muted>Free heap</span><div class=metric id=sMemory>-</div></div>
<div><span class=muted>TOU</span><div class=metric id=sTou>-</div></div>
</div><p id=sError class="error bad"></p></section>
<section class=card><h2>Charger status</h2><div class=grid>
<div><span class=muted>Connection</span><div><span class=pill id=cConnection>Unknown</span></div></div>
<div><span class=muted>State code</span><div class=metric id=cState>-</div></div>
<div><span class=muted>Voltage</span><div class=metric id=cVoltage>-</div></div>
<div><span class=muted>Current</span><div class=metric id=cCurrent>-</div></div>
<div><span class=muted>Power</span><div class=metric id=cPower>-</div></div>
<div><span class=muted>Limit command</span><div class=metric id=cLimit>-</div></div>
</div><div class="details muted">
<div>Endpoint: <span id=cEndpoint>-</span></div>
<div>Unit: <span id=cUnit>-</span> &middot; Read connector: <span id=cRead>-</span> &middot; Holding connector: <span id=cHolding>-</span></div>
<div>Last Modbus phase: <span id=cPhase>-</span> &middot; Updated: <span id=cUpdated>-</span></div>
</div></section>
<form id=config class=card><h2>Configuration</h2><div class=grid>
<label>Mode<select name=policy_mode><option value=full_green>Full green</option><option value=max_power>Maximum power</option></select></label>
<label>Charger host<input name=charger_host required placeholder=192.168.1.146></label>
<label>Charger port<input name=charger_port type=number min=1 max=65535></label>
<label>Modbus unit ID<input name=charger_unit_id type=number min=0 max=247></label>
<label>Read connector ID<input name=read_connector_id type=number min=0 max=9></label>
<label>Holding connector ID<input name=holding_connector_id type=number min=0 max=9></label>
<label>Minimum amps<input name=charger_min_amps type=number min=6 max=32></label>
<label>Maximum amps<input name=charger_max_amps type=number min=6 max=32></label>
<label>Feedback step (A)<input name=feedback_step_amps type=number min=1 max=10></label>
<label>6 A hold (seconds)<input name=full_green_hold_seconds type=number min=0 max=600></label>
<label>Update interval (seconds)<input name=update_interval_s type=number min=2 max=300></label>
<label>Atmoce station ID<input name=atmoce_station_id type=number min=0></label>
<label>Atmoce username<input name=atmoce_username autocomplete=username></label>
<label>Atmoce password<input name=atmoce_password type=password autocomplete=current-password placeholder="Leave blank to keep saved password"></label>
<label><input name=atmoce_password_encoded type=checkbox> Password is already API/Base64 encoded</label>
<label>Existing bearer token (optional)<input name=atmoce_token type=password placeholder="Leave blank to keep saved token"></label>
<label>SESSION cookie (optional)<input name=atmoce_session type=password placeholder="Usually obtained automatically"></label>
<label>Grid voltage<input name=grid_voltage_v type=number min=100 max=300 step=.1></label>
<label><input name=tou_enabled type=checkbox> Enable TOU maximum-power override</label>
<label>TOU UTC offset (minutes)<input name=tou_utc_offset_minutes type=number min=-720 max=840><small>Bangkok = 420</small></label>
<label>Night start<input name=tou_night_start type=time></label>
<label>Night end<input name=tou_night_end type=time></label>
<label><input name=tou_weekend_saturday type=checkbox> Maximum power all Saturday</label>
<label><input name=tou_weekend_sunday type=checkbox> Maximum power all Sunday</label>
<label>Wi-Fi SSID<input name=wifi_ssid></label>
<label>Wi-Fi password<input name=wifi_password type=password placeholder="Leave blank to keep saved password"></label>
<label>Setup AP password<input name=setup_ap_password type=password placeholder="Leave blank to keep saved password"></label>
</div><p><button type=submit>Save configuration</button></p><div id=message></div></form>
<p class=muted>Atmoce credentials are never returned by the API. Changing Wi-Fi settings reboots the ESP32.</p>
<script>
const $=s=>document.querySelector(s), form=$('#config');let loaded=false;
const fmt=(v,n=1)=>v==null?'-':Number(v).toFixed(n);
async function status(){try{const r=await fetch('/api/status'),s=await r.json();
const bypass=s.policy.effective_mode==='max_power',meter=bypass?'Atmoce bypassed':(s.meter_ok?'Atmoce online':'Atmoce unavailable'),wifiOk=s.wifi.startsWith('Wi-Fi ');
$('#sWifi').textContent=s.wifi;$('#sWifi').className='pill '+(wifiOk?'good':'bad');
$('#sMeter').textContent=meter;$('#sMeter').className='pill '+(bypass?'muted':(s.meter_ok?'good':'bad'));
$('#sControl').textContent=s.control_ok?'Controller OK':'Controller error';$('#sControl').className='pill '+(s.control_ok?'good':'bad');
$('#sMode').textContent=s.policy.effective_mode+(s.policy.tou.active_now?' (TOU)':'');$('#sLimit').textContent=s.ev_limit_amps+' A';$('#sGrid').textContent=fmt(s.meter.grid_raw_amps)+' A';
$('#sBattery').textContent=fmt(s.meter.storage_amps)+' A / '+fmt(s.meter.battery_soc,0)+'%';
$('#sCharger').textContent=s.charger.connected?(fmt(s.charger.current_amps)+' A'):'offline';$('#sMemory').textContent=(s.free_heap_bytes||0)+' B';
$('#sTou').textContent=s.policy.tou.enabled?(s.policy.tou.active_now?'active':(s.policy.tou.local_time||'clock invalid')):'disabled';
const c=s.charger,online=!!c.connected;$('#cConnection').textContent=online?'Connected':'Offline';$('#cConnection').className='pill '+(online?'good':'bad');
$('#cState').textContent=online?(c.state??'-'):'-';$('#cVoltage').textContent=online?fmt(c.voltage_v,1)+' V':'-';
$('#cCurrent').textContent=online?fmt(c.current_amps,2)+' A':'-';$('#cPower').textContent=online?fmt(c.power_w,0)+' W':'-';
$('#cLimit').textContent=s.ev_limit_amps+' A '+(c.limits_written?'sent':'pending');$('#cEndpoint').textContent=c.endpoint||'-';
$('#cUnit').textContent=c.unit_id??'-';$('#cRead').textContent=c.read_connector_id??'-';$('#cHolding').textContent=c.holding_connector_id??'-';
$('#cPhase').textContent=c.modbus_phase||'-';$('#cUpdated').textContent=new Date().toLocaleTimeString();
$('#sError').textContent=s.last_error||''}catch(e){$('#sWifi').textContent='ESP32 unavailable';$('#sWifi').className='pill bad';$('#sMeter').textContent='Meter unknown';$('#sMeter').className='pill muted';$('#sControl').textContent='Controller unknown';$('#sControl').className='pill muted'}}
async function load(){const c=await (await fetch('/api/config')).json();for(const [k,v] of Object.entries(c)){const e=form.elements[k];if(e&&v!=null){if(e.type==='checkbox')e.checked=!!v;else e.value=v}}loaded=true}
form.addEventListener('submit',async e=>{e.preventDefault();const out={};for(const [k,v] of new FormData(form)){out[k]=v}
for(const k of ['charger_port','charger_unit_id','read_connector_id','holding_connector_id','charger_min_amps','charger_max_amps','feedback_step_amps','full_green_hold_seconds','update_interval_s','atmoce_station_id','tou_utc_offset_minutes'])out[k]=parseInt(out[k],10);
for(const k of ['tou_enabled','tou_weekend_saturday','tou_weekend_sunday','atmoce_password_encoded'])out[k]=form.elements[k].checked;
out.grid_voltage_v=parseFloat(out.grid_voltage_v);const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(out)});const b=await r.json();
$('#message').textContent=b.ok?(b.rebooting?'Saved; ESP32 is rebooting...':'Saved'):(b.error||'Save failed');if(b.ok&&!b.rebooting)load()});
load().catch(()=>{});status();setInterval(status,5000);
</script></main></body></html>"""


def _json_bytes(value):
    return json.dumps(value).encode()


async def _reply(writer, code, body, content_type):
    reason = "OK" if code < 400 else "Error"
    header = ("HTTP/1.0 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
              "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
              % (code, reason, content_type, len(body))).encode()
    writer.write(header)
    writer.write(body)
    await writer.drain()


async def _read_request(reader):
    first = await reader.readline()
    if not first:
        return None, None, b""
    parts = first.decode().strip().split()
    if len(parts) < 2:
        raise ValueError("invalid request line")
    headers = {}
    size = len(first)
    while True:
        line = await reader.readline()
        size += len(line)
        if size > MAX_HEADER:
            raise ValueError("headers too large")
        if line in (b"\r\n", b"\n", b""):
            break
        name, separator, value = line.decode().partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length < 0 or length > MAX_BODY:
        raise ValueError("body too large")
    body = bytearray()
    while len(body) < length:
        part = await reader.read(length - len(body))
        if not part:
            raise ValueError("short request body")
        body.extend(part)
    body = bytes(body)
    return parts[0], parts[1].split("?", 1)[0], body


class WebServer:
    def __init__(self, app):
        self.app = app

    async def handle(self, reader, writer):
        try:
            method, path, body = await _read_request(reader)
            if method == "GET" and path in ("/", "/index.html"):
                await _reply(writer, 200, HTML, "text/html; charset=utf-8")
            elif method == "GET" and path == "/api/status":
                await _reply(writer, 200, _json_bytes(self.app.status()), "application/json")
            elif method == "GET" and path == "/api/config":
                await _reply(writer, 200, _json_bytes(config_store.public_config(self.app.cfg)), "application/json")
            elif method == "POST" and path == "/api/config":
                result = self.app.update_config(json.loads(body.decode()))
                await _reply(writer, 200, _json_bytes(result), "application/json")
            elif method == "GET" and path == "/health":
                await _reply(writer, 200, b'{"ok":true}', "application/json")
            else:
                await _reply(writer, 404, b'{"ok":false,"error":"not found"}', "application/json")
        except Exception as exc:
            await _reply(writer, 400, _json_bytes({"ok": False, "error": str(exc)}), "application/json")
        finally:
            try:
                await writer.wait_closed()
            except (AttributeError, OSError):
                try:
                    writer.close()
                except OSError:
                    pass

    async def start(self, port=80):
        return await asyncio.start_server(self.handle, "0.0.0.0", port)
