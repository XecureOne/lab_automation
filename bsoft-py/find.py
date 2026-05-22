import json
import random

def fetch_ips():
    with open("ip_map.json","r") as f:
        res = json.load(f)
        return res

def fetch_active_sessions():
    with open("../leaky/static_ips.json","r") as f:
        res = json.load(f)
        return res

def find_active_accounts():
    ips = fetch_ips()
    active_sessions = fetch_active_sessions()
    
    ip = ""
    while(True):
        ip = random.choice(list(ips.values()))
        if ip not in list(active_sessions.values()):
            break

    print(ip)

find_active_accounts()
            



