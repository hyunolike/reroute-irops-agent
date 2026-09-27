"""Soundtrack for the ReRoute brag video: A minor, 112 BPM, music + SFX written as one piece."""
import numpy as np, wave

SR = 48000
DUR = 21.0
N = int(SR * DUR)
BPM = 112
BEAT = 60 / BPM
T0 = 3.0  # groove starts when the product appears (scene 2)
rng = np.random.default_rng(7)

def midi(m): return 440.0 * 2 ** ((m - 69) / 12)
def env_adsr(n, a, d, s, r, sus_len):
    a, d, r = int(a * SR), int(d * SR), int(r * SR)
    sl = max(0, n - a - d - r)
    e = np.concatenate([np.linspace(0, 1, max(a, 1)), np.linspace(1, s, max(d, 1)), np.full(sl, s), np.linspace(s, 0, max(r, 1))])
    return np.pad(e, (0, max(0, n - len(e))))[:n]

def lowpass(x, fc, order=2):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(X / (1 + (f / fc) ** (2 * order)) ** 0.5, len(x))

def highpass(x, fc, order=2):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR) + 1e-9
    return np.fft.irfft(X / (1 + (fc / f) ** (2 * order)) ** 0.5, len(x))

def reverb(x, seconds=2.2, damp=5000):
    n = int(seconds * SR)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    ir = lowpass(ir, damp)
    ir /= np.sqrt(np.sum(ir ** 2))
    L = len(x) + n
    y = np.fft.irfft(np.fft.rfft(x, L) * np.fft.rfft(ir, L), L)[:len(x)]
    return y

def place(buf, sig, t, gain=1.0):
    i = int(t * SR)
    if i >= len(buf): return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[:j - i] * gain

def tone(freq, dur, harmonics=((1, 1.0),), detune=0.0):
    t = np.arange(int(dur * SR)) / SR
    s = np.zeros_like(t)
    for h, a in harmonics:
        s += a * np.sin(2 * np.pi * freq * h * t)
        if detune: s += a * np.sin(2 * np.pi * freq * h * (1 + detune) * t)
    return s

# --- buses
pad = np.zeros(N); bass = np.zeros(N); drums = np.zeros(N); keys = np.zeros(N); sfx = np.zeros(N)

# Chords: Am F C G (one bar each), voiced around A3–E5
CH = {'Am': [57, 60, 64, 69], 'F': [53, 57, 60, 65], 'C': [55, 60, 64, 67], 'G': [55, 59, 62, 67]}
ROOT = {'Am': 45, 'F': 41, 'C': 48, 'G': 43}
BAR = 4 * BEAT
prog_ = ['Am', 'F', 'C', 'G']

# Hook (0–3s): low Am pad swell only
for m in CH['Am']:
    s = tone(midi(m), 3.4, ((1, 1), (2, .25), (3, .08)), detune=0.003)
    place(pad, s * env_adsr(len(s), 1.2, .5, .8, 1.0, 0), 0.0, .10)

# Groove from T0 to outro
n_bars = int(np.ceil((18.2 - T0) / BAR))
for b in range(n_bars):
    t = T0 + b * BAR
    c = prog_[b % 4]
    for m in CH[c]:
        s = tone(midi(m), BAR + .6, ((1, 1), (2, .3), (3, .1), (4, .04)), detune=0.004)
        place(pad, s * env_adsr(len(s), .25, .4, .7, .6, 0), t, .075)
    # sub bass: root on beats 1 and 3, fifth pickup on the "and" of 4
    for k, (off, m, d) in enumerate([(0, ROOT[c], 1.4), (2, ROOT[c], 1.4), (3.5, ROOT[c] + 7, .4)]):
        s = tone(midi(m), d * BEAT, ((1, 1), (2, .35)))
        place(bass, s * env_adsr(len(s), .01, .15, .6, .12, 0), t + off * BEAT, .32)

# Drums
def kick():
    n = int(.35 * SR); t = np.arange(n) / SR
    f = 42 + 90 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
def hat():
    n = int(.06 * SR)
    return highpass(rng.standard_normal(n), 7000) * np.exp(-np.linspace(0, 8, n))
def clap():
    n = int(.25 * SR)
    s = lowpass(highpass(rng.standard_normal(n), 900), 5000) * np.exp(-np.linspace(0, 10, n))
    return s

nb = int((18.2 - T0) / BEAT)
for i in range(nb):
    t = T0 + i * BEAT
    place(drums, kick(), t, .55)
    if i % 2 == 1: place(drums, clap(), t, .10)
    place(drums, hat(), t + BEAT / 2, .06)
    if i >= 8: place(drums, hat(), t + BEAT / 4 * 3, .03)

# Keys: soft plucks in A minor pentatonic, synced to on-screen events
def pluck(m, dur=.9, g=1.0):
    s = tone(midi(m), dur, ((1, 1), (2, .5), (3, .2), (5, .05)))
    return s * env_adsr(len(s), .004, .25, .2, .5, 0) * g

# timeline steps finish (s.at + .3)
for t, m in zip([7.25, 7.65, 8.1, 8.55, 9.0, 9.45], [69, 72, 74, 76, 79, 81]):
    place(keys, pluck(m), t, .20)
# comparison rows land
for t, m in zip([12.2, 12.5, 12.8], [76, 79, 81]):
    place(keys, pluck(m, 1.2), t, .22)

# --- SFX (same key, same room)
# CANCELLED stamp: low A thud + short filtered noise
n = int(.8 * SR); tt = np.arange(n) / SR
boom = np.sin(2 * np.pi * np.cumsum(midi(33) + 60 * np.exp(-tt * 25)) / SR) * np.exp(-tt * 5)
boom += lowpass(rng.standard_normal(n), 600) * np.exp(-tt * 18) * .5
place(sfx, boom, 0.95, .6)
# riser into scene 2
n = int(1.0 * SR)
rise = lowpass(rng.standard_normal(n), 3000) * np.linspace(0, 1, n) ** 2
place(sfx, highpass(rise, 400), 2.0, .05)

# typing ticks: tiny, background
for i, t in enumerate(np.arange(3.6, 5.1, 0.07)):
    n = int(.02 * SR)
    tk = highpass(rng.standard_normal(n), 3000) * np.exp(-np.linspace(0, 10, n))
    place(sfx, tk, t + rng.uniform(-.01, .01), .025 + .01 * (i % 3 == 0))

def click(m=81):
    s = tone(midi(m), .12, ((1, 1), (2, .3)))
    return lowpass(s * np.exp(-np.linspace(0, 12, len(s))), 4000)
place(sfx, click(), 5.6, .22)      # Run Agent
place(sfx, click(76), 15.55, .18)  # bypass button
# 403: two low muted notes (E -> A, falling), soft
for t, m in [(15.64, 52), (15.78, 45)]:
    s = tone(midi(m), .35, ((1, 1), (2, .5), (3, .3)))
    place(sfx, lowpass(s * env_adsr(len(s), .005, .1, .3, .2, 0), 1200), t, .22)
place(sfx, click(), 16.55, .22)    # Approve
# approval chime: A5 + E6
for t, m in [(16.62, 81), (16.74, 88)]:
    place(sfx, pluck(m, 1.6), t, .16)
# final report count
for i, t in enumerate(np.linspace(16.9, 17.5, 6)):
    place(sfx, pluck(84 + [0, 3, 5, 7, 10, 12][i], .3), t, .04)

# Outro: Am add9 pad + bass, bell on logo
for m in [45, 57, 60, 64, 69, 71, 76]:
    s = tone(midi(m), 3.0, ((1, 1), (2, .3), (3, .1)), detune=.003)
    place(pad, s * env_adsr(len(s), .15, .6, .7, 1.4, 0), 18.2, .07 if m > 50 else .25)
place(drums, kick(), 18.2, .6)
for t, m in [(18.3, 81), (18.45, 88), (18.6, 93)]:
    place(keys, pluck(m, 2.0), t, .12)

# --- mix
pad = lowpass(pad, 2500)
bass = lowpass(bass, 400)
music = pad + bass + drums * .9 + keys
wet_src = pad * .6 + keys + sfx * .8 + drums * .15
mix = music + sfx + reverb(wet_src, 2.4) * .35
mix = highpass(mix, 30)
# gentle master: normalize then soft clip
mix /= np.max(np.abs(mix)) + 1e-9
mix = np.tanh(mix * 1.4) / np.tanh(1.4) * .89
# fades
f = int(.02 * SR); mix[:f] *= np.linspace(0, 1, f)
f = int(1.2 * SR); mix[-f:] *= np.linspace(1, 0, f) ** 1.5
st = np.stack([mix, mix], 1)
# slight stereo width from reverb decorrelation
wide = reverb(wet_src, 2.4) * .08
wide /= np.max(np.abs(wide)) + 1e-9
st[:, 0] += wide * .05; st[:, 1] -= wide * .05
st = np.clip(st, -1, 1)
with wave.open('music.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((st * 32767).astype(np.int16).tobytes())
print('ok', st.shape)
