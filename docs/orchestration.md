# Herdr: Claude điều phối agy workers

Claude (pane `w4:p1`) là supervisor. Các agy là worker, chạy trong cùng workspace.
Điều kiện: Claude phải chạy trong pane Herdr (`HERDR_ENV=1`).

Nguồn chính sách orchestrator (token, việc nào giao/giữ, `agy-ask`): `CLAUDE.md`
và skill `.claude/skills/agy-orchestrator/SKILL.md`. File này chỉ bổ sung phần thực hành đã kiểm chứng; khi mâu thuẫn, skill thắng.
Việc một lần, tự chứa: dùng `agy-ask accN "<prompt>" [--mode ...] [--max N]` (không mở pane) trước, pane chỉ khi cần nhiều bước/giám sát.

## Cấu trúc đã dựng

- Mỗi tài khoản là một profile cô lập: `~/.agy_profiles/accN/` (HOME, D-Bus, keyring riêng) → key và quota tách biệt.
- Khởi chạy worker trong một ô shell trống: `agy-as accN [--mode plan|accept-edits|auto]`.
  `agy-up accN` thì tạo workspace mới (không dùng ô có sẵn), chỉ dùng khi muốn workspace riêng.
- Skill dùng chung: kho `~/.gemini/config/skills/`, mỗi profile có symlink tới
  ponytail, ponytail-review, systematic-debugging, test-driven-development, verification-before-completion.
  Thêm skill mới: bỏ vào kho, rồi `ln -sfn ~/.gemini/config/skills/<tên> ~/.agy_profiles/accN/.gemini/config/skills/<tên>`.
- Git: `~/.agy_profiles/accN/.gitconfig` → `agy-accN <agy-accN@local>`. Worker chỉ **commit cục bộ**, supervisor/người review rồi push.
  Worker không dùng được SSH key/`gh` login của máy chính.
- Luật cho worker (trả lời ngắn, đọc có chọn lọc, phạm vi, commit cục bộ): nguồn `~/.gemini/worker-rules/GEMINI.md`,
  symlink thành `~/.agy_profiles/accN/.gemini/GEMINI.md`. Sửa một chỗ, áp cho mọi worker; giới hạn 24 KB/file.

## Role và tên agent

Khai báo ở `~/.agy_profiles/roles.conf` (acc, role, mode mặc định). Tên agent = `<acc>-<role>`:

| Agent | Role | Mode mặc định | Giao việc |
|---|---|---|---|
| acc1-architect | thiết kế/kế hoạch | plan | spec, phân tích, kế hoạch (chưa chạy) |
| acc2-impl | cài đặt chính | accept-edits | viết/sửa code theo spec |
| acc3-qa | tính năng phụ + test | accept-edits | viết test theo spec, chạy bộ test, việc cơ học |
| acc4-review | review/đọc | plan | đọc code, review diff, tìm kiếm (không sửa) |

Sau mỗi lần (re)start agent: chạy `agy-sync`. Nó nhận tài khoản của từng ô qua `HOME` của tiến trình, đặt tên theo role và đưa
về mode mặc định (chỉ với agent idle). Đổi role: sửa `roles.conf` rồi `agy-sync`. Chia việc theo **tên/role**, không theo pane ID.
Role chỉ là quy ước tên + mode; ràng buộc thật là prompt (phạm vi, skill) và mode.

## Điều phối (lệnh Herdr)

```bash
herdr agent list                                   # agent, pane, trạng thái
herdr agent rename <pane-id> w1                    # đặt tên (mất khi agent thoát)
herdr agent prompt w1 "<việc>" --wait --timeout 150000
herdr agent read w1 --source recent-unwrapped --lines 60   # kết quả là text, không phải JSON
herdr agent send-keys w1 esc                       # đóng dialog/overlay
```

- Trạng thái: `idle`/`done` = sẵn sàng; `working`; `blocked` = đang hỏi quyền → hỏi người dùng trước khi trả lời; `unknown`.
- agy có thể `idle` trong khi subagent/task nền vẫn chạy → luôn đọc kết quả thật.
- Chạy song song: nhiều `agent prompt ... --wait &` rồi `wait`. Chia theo file/module riêng để không đè nhau.
- ID pane đổi khi di chuyển pane/workspace; luôn chạy lại `herdr agent list`.
- Không đóng pane/workspace không do mình tạo; không `herdr server stop`.

## Đổi mode agy

Mode ngay trong phiên: `Shift+Tab` xoay vòng
`default (không nhãn) → accept-edits → auto → plan → default`. Nhãn mode hiện ở dòng `? for shortcuts` cuối màn hình.

```bash
agy-mode w1 plan            # default | accept-edits | auto | plan  (chỉ dùng khi agent idle)
```

Ghi chú: `agy-up acc --mode plan` đã chạy nhưng agent lên ở mode default (không nhãn) → kiểm tra bằng `agy-mode`, đừng tin cờ khởi động.
Mode theo nhiệm vụ: nghiên cứu/lập kế hoạch → `plan`; sửa code có kiểm soát → `accept-edits`; việc lặp, rủi ro thấp → `auto`.

## Mẫu giao việc cho worker

```
Mục tiêu: <1 câu>
Phạm vi: chỉ sửa trong <thư mục/file>. Không đụng file khác.
Skill: dùng <systematic-debugging | test-driven-development | ...>.
Kết thúc: chạy test, commit cục bộ (git add + commit), báo: file đã đổi, kết quả test, điều chưa chắc.
```

Quy trình mỗi task: chọn worker `idle` → đặt mode phù hợp (`agy-mode`) → `agent prompt --wait` → `agent read` → kiểm tra thật (`git diff`, chạy test) → tổng hợp cho người dùng.

## Đã kiểm chứng / chưa

- Đã: giao việc song song 3 worker, đọc kết quả, `$HOME` cô lập theo profile, đổi mode bằng `agy-mode`,
  `agy-sync` đặt tên/mode theo role, acc2/acc3/acc4 đều thấy đủ 5 skill và luật `GEMINI.md`,
  commit local của acc3 mang danh tính `agy-acc3 <agy-acc3@local>`.
- Chưa: luật có làm worker trả lời ngắn hơn thật không (đo trên task thật); acc1 chưa chạy (role architect mới trên giấy).
