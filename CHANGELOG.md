# Changelog

## 0.1.0 (2026-09-12)


### Bug Fixes

* hold the first clean frame for the marker flash so video t=0 lands on audio t=0 ([65f20db](https://github.com/jacobcbeaudin/decktalk/commit/65f20db591ed5266fd57b1192a878dfc5a22a8d7))
* make the cue check robust to camera drift and font rendering; cache browser downloads in CI ([dfd2477](https://github.com/jacobcbeaudin/decktalk/commit/dfd247744d365d973c4c4d5e79d1e7a40a4562fb))


### Performance Improvements

* seek both inputs in the cue comparison instead of decoding from the start ([26e93df](https://github.com/jacobcbeaudin/decktalk/commit/26e93df6b144489ba8a5c55c34e471d0b42280d7))


### Documentation

* rewrite the README and docs in plain declarative prose ([c90e35f](https://github.com/jacobcbeaudin/decktalk/commit/c90e35fb15aa1d6445c312e30f0346384f7b4e3b))
* title the landing page Overview so the tab does not repeat the site name ([6e60028](https://github.com/jacobcbeaudin/decktalk/commit/6e6002812386447d974747e7fc7cc979d515521a))


### Continuous Integration

* adopt release-please, conventional commits, and a lighter pull-request matrix ([80d0491](https://github.com/jacobcbeaudin/decktalk/commit/80d04910e02ba8c8d22face0e77d06c9a2bc9ae3))
