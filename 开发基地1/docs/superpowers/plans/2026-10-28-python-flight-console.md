# Python Flight Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Provide a manual Python console for Neo 2 takeoff, brief forward movement, and landing over the same UDP session.

**Architecture:** A pure protocol module wraps the captured DUML flight frame and recognizes the captured flight response. An interactive console owns one UDP socket, performs the proven handshake, sends bounded background neutral traffic, and accepts explicit user commands. The UI distinguishes a handshake reply from a flight-controller reply; it never reports flight success from a socket send alone.

**Tech Stack:** Python standard library plus existing `pcapng`-backed offline analysis tools and workspace virtual environment.

**Spec:** `开发基地1/飞行控制指令现状.md` and `captures/takeoff-forward-land-labeled-20261028.pcapng`.

## Global Constraints

- The captured takeoff command is `02→03, 03/2A, flag 40, payload 01`; landing uses payload `02`.
- The captured forward packet has roll/throttle/yaw at 1024 and pitch above 1024; foreground commands never use pitch outside 364–1684.
- The flight tunnel has separate inner and outer sequence numbers and the observed five-byte trailer prefix `8e338dbb02`.
- A UDP send does not mean the drone accepted a command. Only a matching inner flight reply is an acknowledgment.
- No command is sent without the user first connecting to drone Wi-Fi and typing the action in the console.

## Review Focus

- Wrong network route: refuse to start unless the local source address is in `192.168.2.0/24` and is not `192.168.2.1`.
- Wrong session or sequence in a reply: ignore it when matching a flight acknowledgment.
- Stuck forward command: always return to neutral after the bounded interval, including on interruption.
- No takeoff reply: do not enable forward movement; allow land as a recovery command.
- Socket or link failure: stop sending movement, report the failure, and close cleanly.

---

### Task 1: Flight packet and reply parser

**Files:** Create `tools/control_packets.py`; create `tools/test_control_packets.py`.

**Interfaces:** `build_fly_packet(session, seed, ack, word, outer_seq, inner_seq, param) -> bytes`; `find_fly_reply(raw, session, inner_seq) -> bytes | None`.

- [x] Write a failing test for the captured takeoff frame inside a valid channel-4 packet and for a matching nested reply.
- [x] Run the test to confirm it fails.
- [x] Implement packet wrapping with the captured 34-byte app prefix and nested-reply matching.
- [x] Run the focused test.

### Task 2: Manual console with bounded movement

**Files:** Create `tools/flight_console.py`; create `tools/test_flight_console.py`; update `README.md`.

**Interfaces:** `FlightController` owns a socket and exposes `connect()`, `takeoff()`, `forward(seconds)`, `land()`, `close()`; `main()` provides the manual console.

- [x] Write loopback tests for handshake, matching takeoff reply, no-reply behavior, and forward auto-neutral.
- [x] Run the tests to confirm they fail.
- [x] Implement the single-socket controller and bounded control commands.
- [x] Run focused and full offline tests; run a no-network preview.
- [x] Document exact user steps and unverified hardware limits.

### Task 3: Final verification

- [x] Confirm the complete suite passes and Python files compile.
- [x] Confirm the computer is not on drone Wi-Fi, so no live command has been sent during implementation.
- [x] Give the user the exact terminal command and interactive action sequence for a supervised hardware attempt.

Ruling: The development base is not a Git repository, so implementation remained in the existing user-requested folder and no worktree or commits were created. The complete offline suite passed; live control remains unverified until the user connects the computer to the aircraft.

