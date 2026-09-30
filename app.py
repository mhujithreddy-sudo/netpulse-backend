from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import platform
import re
import ipaddress
import shutil

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4096

# Local development only. Restrict origins before production.
CORS(app, resources={r'/api/*': {'origins': '*'}})


def valid_target(target):
    if not isinstance(target, str) or not target or len(target) > 253:
        return False

    target = target.strip().rstrip('.')

    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        pass

    # Basic hostname validation
    pattern = (
        r'(?=.{1,253}$)'
        r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}'
        r'[A-Za-z0-9])?)'
        r'(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}'
        r'[A-Za-z0-9])?))*'
    )
    return bool(re.fullmatch(pattern, target))


def get_target():
    body = request.get_json(silent=True) or {}
    target = body.get('target', '')

    if not valid_target(target):
        return None

    return target.strip().rstrip('.')


@app.get('/api/health')
def health():
    return jsonify(
        success=True,
        message='Ping and Traceroute API is running',
        platform=platform.system()
    )

@app.get('/api/debug')
def debug_os():
    import subprocess
    try:
        res = subprocess.run(['cat', '/etc/os-release'], capture_output=True, text=True)
        return res.stdout
    except Exception as e:
        return str(e)



@app.post('/api/ping')
def ping():
    target = get_target()

    if not target:
        return jsonify(
            success=False,
            error='Enter a valid hostname or IP address.'
        ), 400

    try:
        data = request.get_json(silent=True) or {}
        count = int(data.get('count', 4))
        count = max(1, min(20, count))
    except (ValueError, TypeError):
        count = 4

    if platform.system() == 'Windows':
        command = ['ping', '-n', str(count), '-w', '2000', target]
    else:
        command = ['ping', '-c', str(count), '-W', '2', target]

    if not shutil.which(command[0]):
        return jsonify(
            success=False,
            error='Ping command is unavailable on this server.'
        ), 500

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
            errors='replace'
        )

        output = (
            result.stdout + '\n' + result.stderr
        ).strip()[:12000]

        loss = re.search(
            r'(\d+(?:\.\d+)?)%\s*packet loss',
            output,
            re.I
        )

        if not loss:
            loss = re.search(
                r'\((\d+)%\s*loss\)',
                output,
                re.I
            )

        times = [
            float(x) for x in re.findall(
                r'time[=<]\s*(\d+(?:\.\d+)?)\s*ms',
                output,
                re.I
            )
        ]

        stats = {
            'packet_loss_percent': (
                float(loss.group(1)) if loss else None
            ),
            'packets_sent': count,
            'packets_received': len(times),
            'min_ms': min(times) if times else None,
            'avg_ms': (
                round(sum(times) / len(times), 2)
                if times else None
            ),
            'max_ms': max(times) if times else None
        }

        return jsonify(
            success=True,
            data={
                'target': target,
                'reachable': bool(times),
                'statistics': stats,
                'raw_output': output
            }
        )

    except subprocess.TimeoutExpired:
        return jsonify(
            success=False,
            error='Ping timed out.'
        ), 504

    except Exception:
        app.logger.exception('Ping failed')
        return jsonify(
            success=False,
            error='Ping could not be completed.'
        ), 500


@app.post('/api/traceroute')
def traceroute():
    target = get_target()

    if not target:
        return jsonify(
            success=False,
            error='Enter a valid hostname or IP address.'
        ), 400

    if platform.system() == 'Windows':
        executable = 'tracert'
        command = [
            'tracert', '-h', '15',
            '-w', '1500', target
        ]
    else:
        executable = 'traceroute'
        command = [
            'traceroute', '-m', '15',
            '-w', '2', target
        ]

    if not shutil.which(executable):
        return jsonify(
            success=False,
            error=f'{executable} command is unavailable on this server.'
        ), 500

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=40,
            shell=False,
            errors='replace'
        )

        output = (
            result.stdout + '\n' + result.stderr
        ).strip()[:20000]

        hops = []

        for line in output.splitlines():
            # Match start of line with hop number
            match = re.match(r'^\s*(\d+)\s+(.*)$', line)
            if not match:
                continue

            number = int(match.group(1))
            rest = match.group(2)
            
            # Times are usually float followed by ms
            times = [float(x) for x in re.findall(r'<?(\d+(?:\.\d+)?)\s*ms', rest, re.I)]
            
            # For timeouts, rest might be "* * *"
            if rest.replace('*', '').strip() == '':
                hops.append({
                    'hop': number,
                    'address': '*',
                    'hostname': None,
                    'times_ms': [],
                    'timeout': True,
                    'status': 'Timeout'
                })
                continue
                
            # Try to extract hostname and IP
            # Usually format is: hostname (ip) time ms
            # Or just: ip time ms
            host_ip_match = re.search(r'([\w\.-]+)\s+\(([\d\.]+)\)', rest)
            hostname = None
            address = None
            
            if host_ip_match:
                hostname = host_ip_match.group(1)
                address = host_ip_match.group(2)
                if hostname == address:
                    hostname = None
            else:
                ip_match = re.search(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', rest)
                if ip_match:
                    address = ip_match.group(0)
                    
            if not address and '*' in rest:
                address = '*'
                
            hops.append({
                'hop': number,
                'address': address,
                'hostname': hostname,
                'times_ms': times,
                'timeout': not bool(times),
                'status': 'Responding' if times else 'Timeout'
            })

        return jsonify(
            success=True,
            data={
                'target': target,
                'hops': hops,
                'raw_output': output
            }
        )

    except subprocess.TimeoutExpired:
        return jsonify(
            success=False,
            error='Traceroute timed out. Try again.'
        ), 504

    except Exception:
        app.logger.exception('Traceroute failed')
        return jsonify(
            success=False,
            error='Traceroute could not be completed.'
        ), 500


import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False
    )