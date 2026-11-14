"""SkyGuard Adaptive AI Companion: the local runtime of the SkyGuard Adaptive AI Browser Extension.

A small HTTP service on 127.0.0.1 that only the paired SkyGuard extension can use. It runs the same agent core as
SkyGuard (``skyguard_agent``): the knowledge database, learning, business rules, the execution engine, the audit
trail and the credential vault (Windows DPAPI, current user). It talks to a local Qwen model (llama-server) and a
local Faster-Whisper speech server; nothing leaves this PC and no cloud service is used.

It does not need SkyGuard. When SkyGuard is installed on the same PC, its local Qwen and Faster-Whisper servers can
be reused instead of starting second copies.
"""

VERSION = "1.1.0"
NAME = "SkyGuard Adaptive AI Companion"
