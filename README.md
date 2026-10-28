# Agent Farm

Cách dùng **Herdr** để một agent giám sát (Claude Code) điều phối nhiều worker **Antigravity CLI (`agy`)**, mỗi worker một tài khoản Google cô lập.
Repo này chỉ chứa tài liệu và skill, không có ứng dụng. Phiên bản web hub cũ đã bỏ (xem lịch sử git nếu cần).

## Bức tranh chung

```
Claude Code (supervisor, trong pane Herdr)
   │  herdr agent prompt / read / wait      agy-ask (việc một lần, không mở pane)
   ▼
acc2-impl      acc3-qa        acc4-review     ...   mỗi worker = 1 profile ~/.agy_profiles/accN
(accept-edits) (accept-edits) (plan)                (HOME, D-Bus, keyring, quota riêng)
```

- Supervisor giữ việc khó; worker nhận việc nặng nhưng cơ học. Luật trả lời ngắn cho worker nằm ở `worker-rules/GEMINI.md`.
- Tên agent = `<acc>-<role>`, mode mặc định theo role; `agy-sync` đặt lại sau mỗi lần khởi động.

## Đọc theo thứ tự

| File | Nội dung |
|---|---|
| [docs/setup.md](docs/setup.md) | Cài đặt từ đầu: profile, đăng nhập, skill dùng chung, git, luật worker |
| [docs/orchestration.md](docs/orchestration.md) | Lệnh Herdr, role, đổi mode, mẫu giao việc, điều đã/chưa kiểm chứng |
| [docs/scripts.md](docs/scripts.md) | Mã nguồn `agy-as`, `agy-up`, `agy-mode`, `agy-sync`, `roles.conf`; cách dùng `agy-ask`/`claude-ask` (nguồn: `bin/worker-ask`) |
| [.claude/skills/agy-orchestrator/SKILL.md](.claude/skills/agy-orchestrator/SKILL.md) | Skill cho Claude Code khi điều phối |
| [.claude/skills/office-rules/SKILL.md](.claude/skills/office-rules/SKILL.md) | Skill nội quy office: đọc, kiểm, đề xuất, xử lý khi bị từ chối (cài: `ln -s "$PWD/.claude/skills/office-rules" ~/.claude/skills/`) |
| [bin/office_rules.py](bin/office_rules.py), [bin/rules-hook](bin/rules-hook) | Nội quy office: bản Python của `shared/rules.ts` (Habblaud) và hook PreToolUse (Claude + agy); plugin agy [agy-plugin/office-rules](agy-plugin/office-rules) |
| [worker-rules/GEMINI.md](worker-rules/GEMINI.md) | Luật tiết kiệm token cho worker agy |
| [boss/AGENTS.md](boss/AGENTS.md) | Luật của boss agy: Habblaud mở boss trong thư mục office với `AGY_BOSS=1`, plugin [agy-plugin/agy-boss](agy-plugin/agy-boss) nạp file này (agy mở tay trong `boss/` cũng nạp, là luật thư mục); boss giao việc bằng `agy-ask`/`claude-ask` |
| [CLAUDE.md](CLAUDE.md) | Chỉ dẫn vai trò orchestrator cho Claude Code |

Yêu cầu: Linux, `herdr` (đã thử 0.9.5), `agy`, `dbus-daemon`, `gnome-keyring-daemon`, `python3`.
