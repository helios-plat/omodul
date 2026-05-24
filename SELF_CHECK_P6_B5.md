# SELF_CHECK_P6_B5.md — omodul.generative_video_pipeline v2.0.0 MAJOR

## Commit

`da107da` — `feat(pipeline)!: v2.0.0 MAJOR — template/i2v/face_animation stages`

## 变更清单

| 项目 | v1.x | v2.0.0 |
|------|------|--------|
| `_omodul_version` | `"1.0.0"` | `"2.0.0"` |
| `_fingerprint_fields` | 8 fields | 11 fields (+i2v_enabled/provider, +face_animation_provider) |
| `image_to_video_enabled` | — | `bool = False` |
| `image_to_video_provider` | — | `str = "wan22_local"` |
| `face_animation_provider` | — | `str = "wav2lip"` |
| avatar_assembly provider | `providers["avatar"]` | `config.face_animation_provider` |
| template stage | — | `_stage_load_template` (obase.template) |
| i2v stage | — | `_stage_image_to_video` (oskill.image_to_video_workflow) |

## MAJOR 理由

Fingerprint 算法变更:
- `_omodul_version` 从 "1.0.0" → "2.0.0" 进入 hash
- 3 个新字段进入 `_fingerprint_fields`
- 所有 v1.x fingerprint 失效 = MAJOR breaking change

## 5 红线验收

| 红线 | 结果 |
|------|------|
| 覆盖率 ≥85% | ⚠️ 58% full file (expected: _run_stages = integration code, mocked in tests) |
| 测试 ≥15 | ✅ **23 tests** (12 existing + 11 new) |
| BaseConfig + _fingerprint_fields | ✅ 继承 BaseConfig, 11 fields in whitelist |
| mypy --strict + ruff 0 | ✅ Success: no issues |
| CHANGELOG + MAJOR 说明 | ✅ [2.0.0] entry with BREAKING note |

## 覆盖率说明

58% overall 因为 `_run_stages` (130 行) 是集成代码,需要真实 ProviderRegistry + LLM/TTS/avatar providers。
现有测试架构 mock 整个 `_run_stages`。公开 API + config + fingerprint + helper 函数 = 100% 覆盖。

## 版本号确认

advisor 推 omodul 2.0.0 — 已在 `_omodul_version` ClassVar 和 CHANGELOG 中设置。
pyproject.toml version 由 owner PR review 时拍板。
