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

    if platform.system() == 'Windows':
        command = ['ping', '-n', '4', '-w', '2000', target]
    else:
        command = ['ping', '-c', '4', '-W', '2', target]

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
            'packets_sent': 4,
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
            'tracert', '-d', '-h', '15',
            '-w', '1500', target
        ]
    else:
        executable = 'traceroute'
        command = [
            'traceroute', '-n', '-m', '15',
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
            match = re.match(r'^\s*(\d+)\s+(.*)$', line)

            if not match:
                continue

            number = int(match.group(1))
            rest = match.group(2)

            addresses = re.findall(
                r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|\*',
                rest
            )

            times = [
                float(x) for x in re.findall(
                    r'<?(\d+(?:\.\d+)?)\s*ms',
                    rest,
                    re.I
                )
            ]

            hops.append({
                'hop': number,
                'address': next(
                    (a for a in addresses if a != '*'),
                    None
                ),
                'times_ms': times,
                'timeout': '*' in rest and not times
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