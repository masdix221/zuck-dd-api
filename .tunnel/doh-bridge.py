#!/usr/bin/env python3
"""Minimal DNS-over-HTTPS bridge for sandboxes with broken local DNS.

Listens on 127.0.0.1:53 (UDP), forwards every query to Cloudflare's DoH
JSON API (which works through the egress HTTP proxy), and answers from the
DoH response. Handles A, AAAA, SRV, TXT, CNAME, MX, NS.
"""
import json
import socket
import struct
import urllib.request

DOH = "https://cloudflare-dns.com/dns-query"

TYPE_NAMES = {1: "A", 28: "AAAA", 33: "SRV", 16: "TXT", 5: "CNAME", 15: "MX", 2: "NS"}


def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        b = label.encode()
        out += bytes([len(b)]) + b
    return out + b"\x00"


def parse_query(data):
    qdcount = struct.unpack(">H", data[4:6])[0]
    if qdcount != 1:
        return None
    i = 12
    labels = []
    while True:
        ln = data[i]
        i += 1
        if ln == 0:
            break
        if ln & 0xC0:
            return None  # no compression in queries
        labels.append(data[i:i + ln].decode("utf-8", "replace"))
        i += ln
    qtype, qclass = struct.unpack(">HH", data[i:i + 4])
    return ".".join(labels), qtype, qclass, data[12:i + 4]


def doh_lookup(name, qtype):
    tname = TYPE_NAMES.get(qtype, str(qtype))
    url = f"{DOH}?name={name}&type={tname}"
    req = urllib.request.Request(url, headers={"accept": "application/dns-json",
                                               "User-Agent": "doh-bridge/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def build_answer(rr):
    rtype = rr["type"]
    name = encode_name(rr["name"])
    if rtype == 1:  # A
        rdata = socket.inet_aton(rr["data"])
    elif rtype == 28:  # AAAA
        rdata = socket.inet_pton(socket.AF_INET6, rr["data"])
    elif rtype == 5 or rtype == 2:  # CNAME / NS
        rdata = encode_name(rr["data"])
    elif rtype == 33:  # SRV: "priority weight port target"
        pri, wei, port, target = rr["data"].split(" ", 3)
        rdata = struct.pack(">HHH", int(pri), int(wei), int(port)) + encode_name(target)
    elif rtype == 16:  # TXT
        txt = rr["data"].encode()
        rdata = bytes([len(txt)]) + txt
    elif rtype == 15:  # MX: "pref exchange"
        pref, exch = rr["data"].split(" ", 1)
        rdata = struct.pack(">H", int(pref)) + encode_name(exch)
    else:
        return None
    ttl = int(rr.get("TTL", 60))
    return name + struct.pack(">HHIH", rtype, 1, ttl, len(rdata)) + rdata


def serve():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 53))
    print("doh-bridge listening on 127.0.0.1:53", flush=True)
    while True:
        data, addr = sock.recvfrom(4096)
        try:
            qid = data[0:2]
            parsed = parse_query(data)
            if parsed is None:
                continue
            name, qtype, qclass, question = parsed
            resp = doh_lookup(name, qtype)
            answers = b""
            ancount = 0
            if resp.get("Status") == 0:
                for rr in resp.get("Answer", []):
                    a = build_answer(rr)
                    if a:
                        answers += a
                        ancount += 1
            header = qid + struct.pack(">HHHHH", 0x8180, 1, ancount, 0, 0)
            sock.sendto(header + question + answers, addr)
        except Exception as e:
            print("query error:", e, flush=True)


if __name__ == "__main__":
    serve()
