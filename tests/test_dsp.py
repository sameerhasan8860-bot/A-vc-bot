import numpy as np

from dsp import AudioDSP, DSPSettings


def test_disabled_is_pcm_identity():
    pcm = np.array([0, 1, -1, 12000, -12000], dtype="<i2").tobytes()
    engine = AudioDSP(settings=DSPSettings(enabled=False))
    assert engine.process(pcm) == pcm


def test_neutral_eq_and_valid_pcm_output():
    settings = DSPSettings(amplification_db=0, gain_percent=50)
    engine = AudioDSP(settings=settings)
    samples = np.tile(np.array([0, 1000, -1000, 8000, -8000], dtype="<i2"), 2)
    result = engine.process(samples.tobytes())
    decoded = np.frombuffer(result, dtype="<i2")
    assert decoded.shape == samples.shape
    assert np.isfinite(decoded.astype(np.float64)).all()


def test_parameter_ranges_are_enforced():
    engine = AudioDSP()
    try:
        engine.update(echo_percent=101)
    except ValueError:
        pass
    else:
        raise AssertionError("Out-of-range echo value was accepted")


def test_dsp_effects_preserve_frame_length():
    engine = AudioDSP()
    engine.update(bass_percent=80, treble_percent=70, echo_percent=20, distortion_percent=15)
    pcm = np.random.default_rng(7).integers(-10000, 10000, 1920, dtype=np.int16).astype("<i2").tobytes()
    assert len(engine.process(pcm)) == len(pcm)
