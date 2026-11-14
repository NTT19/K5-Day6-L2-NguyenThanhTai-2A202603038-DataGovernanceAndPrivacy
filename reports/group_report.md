# Báo cáo cá nhân — Day 06 Data Lab: T02 Raw QC Triage

> Báo cáo này mô tả trạng thái thực tế của repo và các output hiện có. Không có metric nào được điền thủ công; khi ground truth chưa được cấp, metric được ghi là chưa thể tính.

## 00 · Thông tin bài làm

| Mục | Giá trị |
|---|---|
| Group ID | G03 (làm cá nhân) |
| Track | T02 — Raw QC Triage |
| Người thực hiện | Nguyễn Thành Tài — owner, implementer, reviewer, report |
| Git URL | [NTT19/K5-Day6-L2-NguyenThanhTai-2A202603038-DataGovernanceAndPrivacy](https://github.com/NTT19/K5-Day6-L2-NguyenThanhTai-2A202603038-DataGovernanceAndPrivacy) (`origin`) |
| Tag demo/final | Chưa tạo |
| Ngày ghi nhận | 2026-11-14 |

## 01 · Bài toán và tiêu chí quyết định

T02 xếp hạng ảnh có nguy cơ lỗi chất lượng — mờ, thiếu sáng hoặc lóa — để con người kiểm tra một lượng ảnh có giới hạn. Người chịu ảnh hưởng là người vận hành dựa vào ảnh và người cần phân tích phương tiện/vật thể trong ảnh. Nếu ảnh lỗi bị bỏ sót, dữ liệu có thể không sử dụng được; nếu ảnh tốt bị báo nhầm, reviewer tốn công và có thể loại bỏ dữ liệu hữu ích.

Đầu vào là ảnh cùng metadata `image_id, path, camera_id, light, split`. Đầu ra là score theo từng `image_id`, danh sách top-20 để review, audit log và quyết định cuối. Policy được cài trong `src/pipeline.py`: thiếu review hoặc có ảnh escalate/skip thì `HOLD`; review đủ và có defect được xác nhận thì `REWORK`; review đủ, không có defect được xác nhận thì `GO`.

## 02 · Dữ liệu và provenance

| Split | Số ảnh | Nguồn | License | Dữ liệu tổng hợp |
|---|---:|---|---|---|
| dev | 56 | `fixtures/t02_raw_qc/`; manifest ghi nguồn `tools/make_fixtures.py`, seed 42 (script tạo fixture thuộc overlay giảng viên) | CC0-1.0 theo `manifest.csv` | Có |
| eval | 40 | Cùng fixture, tách theo scene/group | CC0-1.0 theo `manifest.csv` | Có |
| Tổng | 96 | `images.csv` | CC0-1.0 | Có |

Schema đầu vào có `image_id`, `path`, `camera_id`, `light`, `split`. Manifest ghi nguồn, license, split, group và SHA-256. Cấu hình `strict_provenance: true`; cả baseline Laplacian và PoC đều kết thúc `status=0` sau kiểm tra manifest. Lệnh `python -m tools.verify_manifests` báo **14/14 manifest hợp lệ**, gồm T02. Seed là 42. Không dùng ảnh thật hay dữ liệu cá nhân trong thử nghiệm này.

Trong fixture local hiện không có `fixtures/t02_raw_qc/gt/labels.csv`. Hướng dẫn T02 mô tả ground truth dùng riêng cho evaluator; file nhãn chính thức chưa có trong repo đang chạy. Vì thế không thể tính Recall@20, Precision@20 hoặc các metric theo slice. Không đọc hay tạo nhãn giả để thay thế ground truth.

## 03 · Thiết kế và luồng dữ liệu

Luồng thực hiện: `load` kiểm tra manifest và đọc bảng ảnh → `process` tính feature, chuẩn hóa theo phân phối dev và tạo score → `rank` lấy top 20 → `review` áp dụng quyết định người review, lưu audit → `decide` ghi `GO/HOLD/REWORK` cùng `reason_code`, `reason`, `owner`.

PoC dùng Laplacian variance làm proxy độ nét (thấp hơn thì rủi ro blur cao hơn), `frac_dark` làm tín hiệu thiếu sáng và `frac_saturated` làm tín hiệu lóa. Độ lệch phân phối được chuẩn hóa bằng Q25/Q50/Q75 chỉ từ split dev; sharpness được biến đổi `log1p`. Score tổng hợp là `0.50 × blur_score + 0.30 × dark_score + 0.20 × glare_score`, clip về [0,1]. Thứ hạng cao hơn được đưa lên review trước. Không có model hoặc pretrained weights; xử lý hoàn toàn offline bằng các thư viện cục bộ.

Trọng số và protocol được ghi trong `reports/metric_contract.md` trước khi có metric evaluator. Các tài liệu tham khảo giải thích phép Laplacian và lưu ý focus measure có thể nhạy với điều kiện ảnh; chúng không chứng minh rằng score hiện tại đã đạt chất lượng phân loại.

## 04 · Baseline và PoC

| Run | Pipeline | Seed / pool / budget | Runtime | Diễn giải |
|---|---|---|---:|---|
| Baseline T02 | `src.examples.laplacian_baseline` | 42 / 96 ảnh / queue 20 | 0.5665 s | Chỉ dùng Laplacian variance toàn ảnh; ngưỡng chung lấy từ Q25 của 56 ảnh dev: `222.3122100830078` |
| PoC | `src.pipeline` | 42 / 96 ảnh / queue 20 | 0.5285 s | Feature blur/dark/glare, review thủ công, audit và decision |

Hai run dùng cùng fixture, seed và budget. Baseline xếp hạng theo `score = T / (T + LaplacianVariance)` với `T` là ngưỡng trên; ảnh có variance ≤ T được gợi ý `RECAPTURE`. Baseline không dùng nhãn eval hoặc quyết định của reviewer. PoC dùng ba feature và áp dụng kết quả review vào score cuối; vì vậy so sánh ranking cuối phản ánh cả tác động của review thủ công lên PoC. Runtime tính từ `run_info.json`: baseline `0.5665 / 96 × 100 ≈ 0.5901` giây/100 ảnh; PoC `0.5285 / 96 × 100 ≈ 0.5505` giây/100 ảnh. Đây là thời gian của một lần chạy trên máy local; hai run làm các bước khác nhau nên chưa dùng số đó để kết luận thuật toán nào nhanh hơn.

| Metric | Baseline | PoC | Trạng thái |
|---|---:|---:|---|
| Defect Recall@20 (primary) | Chưa tính | Chưa tính | Thiếu ground truth eval |
| Precision@20 | Chưa tính | Chưa tính | Thiếu ground truth eval |
| False alarm theo `light` / defect-type recall | Chưa tính | Chưa tính | Thiếu ground truth eval |

Lệnh `src.evaluate` cho cả baseline và PoC hiện báo thiếu `fixtures/t02_raw_qc/gt/labels.csv` (exit code 2); không thể tạo metric chính thức khi chưa có overlay. Không tạo `reports/metrics_baseline.json`, `reports/metrics_poc.json` hoặc bảng so sánh bằng cách nhập tay. Khi giảng viên cấp GT/overlay, cần chạy evaluator cho từng prediction rồi mới chạy `src.compare`.

**So sánh mô tả, không phải metric với GT:** Hai queue top-20 trên toàn bộ 96 ảnh trùng **6 ảnh**. Cả 6 ảnh chung đều nằm trong 10 ảnh được reviewer xác nhận lỗi ở queue PoC; 14 ảnh còn lại của baseline chưa được review nên không thể suy ra Precision@20 của baseline. Khi evaluator lọc riêng 40 ảnh `eval` rồi lấy top-20, hai danh sách prediction hiện trùng **10 ảnh**. Danh sách top-20 `eval` của PoC sau override có 6 ảnh đã được reviewer `approve` và 14 ảnh chưa được review; nó khác queue 20 ảnh dùng cho bước human review.

## 05 · Protocol đánh giá

Primary metric là Defect Recall@B với B=20: số ảnh defect trong top-20 chia cho tổng số ảnh defect ở split eval. Secondary metric là Precision@20: số ảnh defect trong top-20 chia cho 20. Ghép prediction và GT bằng `image_id`, chỉ tính split eval, sắp xếp score giảm dần. Miss là ảnh defect nằm ngoài top-20; false alarm là ảnh tốt nằm trong top-20. Báo cáo nên kèm numerator/denominator và khoảng tin cậy Wilson 95% khi có GT; hiện chưa có số liệu để tính.

Pool, seed, budget và trọng số giữ cố định theo `reports/metric_contract.md`: 96 ảnh, seed 42, budget 20, trọng số blur/dark/glare lần lượt 0.50/0.30/0.20. Eval không được dùng để chỉnh score.

## 06 · Kết quả review và failure cases

PoC đã sinh `out/poc/ranked_review.csv`, `review_queue.csv`, `contact_sheet.html`, `review_queue_reviewed.csv`, `audit_log.jsonl`, `decision_log.csv` và `decision.json`. Reviewer Nguyễn Thành Tài đã hoàn tất 20/20 ảnh: 10 `approve` (xác nhận có defect), 10 `reject` (false alarm), 0 `edit`, 0 `escalate`, 0 `skip`. Tỷ lệ ảnh được reviewer xác nhận lỗi trong queue PoC là **10/20 = 50%**; đây là thống kê review nội bộ, không phải Precision@20 chính thức. Bản queue đã review và decision log cũng được chép vào `reports/review_queue.csv` và `reports/decision_log.csv` theo `reports/README.md`.

| Điều kiện sáng | Baseline: số ảnh trong queue 20 | PoC: số ảnh trong queue 20 | PoC `approve` | PoC `reject` |
|---|---:|---:|---:|---:|
| day | 12 | 5 | 2 | 3 |
| dusk | 6 | 4 | 4 | 0 |
| night | 2 | 11 | 4 | 7 |
| **Tổng** | **20** | **20** | **10** | **10** |

Queue PoC gồm 9 ảnh `dev` (4 approve) và 11 ảnh `eval` (6 approve). Các số approve/reject là kết quả đánh giá thủ công của một reviewer trên queue PoC, không phải nhãn chính thức của split eval.

Ví dụ được ghi trong review notes:

- `img_12_3`: reviewer xác nhận ảnh quá tối, nhiều chi tiết đường khó nhìn.
- `img_13_3`: reviewer xác nhận ảnh nhòe, xe và vật thể mất chi tiết.
- `img_06_3`: reviewer đánh dấu false alarm; dù là cảnh đêm, xe và người vẫn nhận diện được. `img_03_2` cũng là ảnh đêm được reviewer bác bỏ.
- `img_22_2`: reviewer xác nhận nhiễu/vỡ khối; ảnh này lọt queue PoC nhưng không nằm trong top-20 Laplacian baseline. PoC hiện chưa có feature riêng cho lỗi nén, nên việc tìm được ảnh này chưa chứng minh hệ thống bắt được mọi lỗi compression.

Đây là đánh giá thủ công trên contact sheet, không phải nhãn ground truth độc lập. Failure mode của PoC là score bị ảnh hưởng bởi độ tối toàn ảnh: **7/11 ảnh night** trong queue bị reviewer bác bỏ. Baseline chỉ dùng độ nét nên có nguy cơ bỏ qua lỗi thiếu sáng hoặc vỡ khối. Có thể thử hiệu chuẩn theo `light`/`camera_id` hoặc thêm feature cho compression, nhưng phải thiết kế trên dev và đánh giá bằng GT heldout trước khi kết luận.

Quyết định cuối trong `out/poc/decision.json` là **REWORK**, `reason_code=CONFIRMED_DEFECTS`, owner `Nguyễn Thành Tài - owner`, vì reviewer xác nhận 10 ảnh cần sửa/thu lại. `decision_log.csv` còn lưu lịch sử lần chạy trước khi review hoàn tất (`HOLD`); quyết định cuối là bản ghi REWORK mới nhất. `audit_log.jsonl` giữ các quyết định và thay đổi review.

## 07 · Tái lập, môi trường và kiểm tra

Lệnh PoC đã chạy thành công, offline, seed 42:

```powershell
.\.venv\Scripts\python.exe -m src.main --config configs/lab.yaml --track t02 --output out/poc --run-name poc --offline --seed 42
```

Lệnh baseline Laplacian đã chạy thành công:

```powershell
.\.venv\Scripts\python.exe -m src.main --config configs/lab.yaml --track t02 --pipeline src.examples.laplacian_baseline --output out/baseline --run-name baseline --offline --seed 42
```

Lệnh evaluate/compare sau khi có GT hợp lệ:

```powershell
.\.venv\Scripts\python.exe -m src.evaluate --track t02 --pred out/baseline/ranked_review.csv --run-name baseline --out reports/metrics_baseline.json
.\.venv\Scripts\python.exe -m src.evaluate --track t02 --pred out/poc/ranked_review.csv --run-name poc --out reports/metrics_poc.json
.\.venv\Scripts\python.exe -m src.compare reports/metrics_baseline.json reports/metrics_poc.json --out reports/table1_baseline_vs_poc.md
```

Môi trường ghi trong `out/poc/run_info.json`: Python 3.15.2, Windows 11, CPU `Intel64 Family 6 Model 140 Stepping 1, GenuineIntel`; run PoC được tạo khi HEAD là `b0ce8bf`, trước commit cá nhân. Mã nguồn và test T02 hiện nằm trong commit `a296de7` (`feat(t02): implement QC triage pipeline and Laplacian baseline`). Baseline `run_info.json` ghi `git_sha: null` do lệnh Git trong môi trường sandbox không nhận quyền sở hữu thư mục; mã nguồn được chạy từ cùng checkout local. README khuyến nghị Python 3.11–3.13, nên môi trường 3.15.2 này cần được đối chiếu khi tái lập ở máy khác.

Kiểm tra lần này: `python -m tools.preflight` có **0 FAIL, 2 WARN** (dữ liệu thật và model tùy chọn không có); `python -m tools.verify_manifests` có **14 manifest hợp lệ, 0 lỗi**; `.\.venv\Scripts\python.exe -m pytest -q` có **48 passed, 39 skipped, 0 failed**. Các test cần GT được skip theo `tests/conftest.py`. Test CLI cũ kỳ vọng exit code 3 đã được cập nhật thành test pipeline thành công; đã bổ sung test từ chối review file có score cũ. Pytest có một cảnh báo ghi cache trong sandbox, không ảnh hưởng exit code 0.

## 08 · Giới hạn và trách nhiệm

Fixture tổng hợp nhỏ, chỉ 96 ảnh và không đại diện đầy đủ cho camera/điều kiện thực tế. Laplacian variance và brightness fractions là proxy; score chưa được xác thực bằng GT và có thể báo nhầm ảnh đêm hoặc bỏ sót lỗi cục bộ. Review chỉ do một người thực hiện nên có rủi ro chủ quan. Không có cơ sở để tuyên bố hệ thống đạt Recall/Precision mục tiêu hay sẵn sàng release. Vì dữ liệu là synthetic và không có PII thật nên rủi ro riêng tư trong thử nghiệm thấp; điều đó không thay thế việc rà soát dữ liệu khi chuyển sang ảnh thật. Báo cáo không đưa ra tuyên bố tuân thủ GDPR hay pháp luật Việt Nam.

## 09 · Đóng góp cá nhân và Git

Nguyễn Thành Tài thực hiện bài theo hình thức cá nhân: cấu hình track/owner/seed, chạy pipeline offline, xem contact sheet, nhập 20 quyết định cùng ghi chú, kiểm tra output, chốt quyết định REWORK và chuẩn bị báo cáo. Pipeline được hoàn thiện với sự hỗ trợ của coding assistant theo yêu cầu của người học; người học thực hiện review ảnh và xác nhận các quyết định được lưu trong output.

`git shortlog -sne --all` có commit cá nhân `a296de7` của `Nguyễn Thành Tài <thanhtai5223@gmail.com>` cho pipeline, baseline, cấu hình và test. Lịch sử repo starter có 6 commit của `loversky02 <bao232030333@lms.utc.edu.vn>` và 4 commit của `Tin Duong <26454564+ivo-gilles@users.noreply.github.com>`; hai tác giả này không phải thành viên bài làm cá nhân. `reports/team_contributions.csv` ghi SHA của commit cá nhân để đối chiếu.

## 10 · Tài liệu tham khảo

| Nguồn | Ngày truy cập | Ý nghĩa với thiết kế |
|---|---|---|
| [OpenCV — Image Filtering: Laplacian](https://docs.opencv.org/4.13.0/d4/d86/group__imgproc__filter.html) | 2026-11-14 | Mô tả Laplacian là tổng đạo hàm bậc hai và kernel 3×3; phù hợp đối chiếu với kernel trong `src/common/image_ops.py`. |
| [Pertuz, Puig & García, “Analysis of focus measure operators for shape-from-focus,” Pattern Recognition (2013)](https://doi.org/10.1016/j.patcog.2012.11.011) | 2026-11-14 | Phân tích các focus measure trong nhiều điều kiện ảnh; nhắc rằng focus proxy cần được đánh giá theo điều kiện dữ liệu, không nên coi một feature là nhãn blur tuyệt đối. |
| [`docs/tracks/T02.md` trong repo](../docs/tracks/T02.md) | 2026-11-14 | Đặc tả nội bộ của lab về input, metric, baseline Laplacian, review budget và failure modes. |

## 11 · Việc còn lại trước khi nộp

- Cần ground truth/overlay do giảng viên cấp để tạo metrics baseline/PoC và bảng so sánh bằng script.
- Commit cá nhân đã có; hai tag `demo-180m` và `final-24h` hiện chưa có. Repo đã có remote `origin`.
- Cần xác minh kết quả trên Python 3.11–3.13 theo môi trường được README khuyến nghị nếu nộp ở máy khác.

Các mục trên được để mở thay vì bịa metric, commit, remote hoặc tag.
