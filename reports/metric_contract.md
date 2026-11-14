# Metric contract — T02 Raw QC Triage

> Protocol hiện tại được ghi nhận trước khi xem kết quả evaluator có ground truth. Không thay pool, budget, seed hoặc trọng số sau khi nhận metric eval.

| Mục | Khai báo của cá nhân thực hiện |
|---|---|
| Track | T02 — Raw QC Triage |
| Primary metric (tên đúng như evaluator) | Defect Recall@B, với B = 20 |
| Tăng tốt hay giảm tốt? | Higher is better |
| Đơn vị đánh giá | Ảnh |
| Công thức + numerator / denominator | Recall@20 = số ảnh lỗi thật thuộc top-20 / tổng số ảnh lỗi thật trong split eval. Numerator là số image_id có is_defect=1 trong top-20; denominator là tổng ảnh có is_defect=1 trong eval. Secondary: Precision@20 = ảnh lỗi thật trong top-20 / 20. |
| Cách ghép prediction ↔ GT | Ghép chính xác theo image_id. Chỉ xét ảnh thuộc eval; sắp score giảm dần, lấy top-20. Không dùng IoU. |
| Candidate pool cố định | fixtures/t02_raw_qc/images.csv: 96 ảnh tổng cộng; 56 dev và 40 eval, chia theo cảnh/scene group. Pool và manifest giữ nguyên. |
| Review budget B / K | B = 20 ảnh |
| Seed | 42 |
| Baseline T02 | Một ngưỡng Laplacian variance toàn ảnh: T = Q25 của 56 ảnh dev = 222.3122100830078 trong run local. Score = T / (T + variance), score cao được review trước; gợi ý RECAPTURE khi variance ≤ T. Quy tắc Q25 được bổ sung sau vòng review thủ công ngày 2026-11-14, trước khi có bất kỳ metric GT chính thức nào; không dùng nhãn eval để chọn ngưỡng. |
| PoC scoring policy (chọn trên dev như thế nào) | Không dùng threshold phân loại nhị phân; xếp hạng theo score. Chuẩn hóa feature bằng các phân vị Q25/Q50/Q75 chỉ từ dev. Blur: log1p(Laplacian variance), giá trị thấp làm tăng risk. Dark: frac_dark cao làm tăng risk. Glare: frac_saturated cao làm tăng risk. Score = 0.50 × blur_score + 0.30 × dark_score + 0.20 × glare_score; các thành phần và score được clip vào [0,1]. Không dùng nhãn hoặc metric eval để chọn trọng số. |
| Eval split (không dùng để tune) | eval (40 ảnh theo images.csv). Chỉ evaluator của giảng viên dùng ground truth; không tune trên eval. |
| Chỉ số lỗi xấu (miss / false alarm / false reject) | Miss: ảnh lỗi thật nằm ngoài top-20. False alarm: ảnh sạch nằm trong top-20. Báo Defect Recall@20 và Precision@20; evaluator còn báo false alarm trong top-20 theo light. |
| Slices sẽ báo cáo (≥2) | light (day/dusk/night): recall và false alarm trong top-20; defect_type: recall theo nhóm lỗi. Có thể bổ sung camera_id như phân tích exploratory nếu có nhãn metadata phù hợp. |
| Cách báo độ bất định khi N nhỏ | Báo numerator, denominator và N cho từng metric/slice; bổ sung Wilson 95% CI cho các tỷ lệ recall/false-alarm khi có ground truth. Evaluator mặc định chỉ in point estimate, CI sẽ được tính riêng và ghi rõ phương pháp. |
| Ngày giờ chốt + người chốt | 2026-11-14 16:12 — Nguyễn Thành Tài, cá nhân thực hiện; |

Nếu baseline = 0 thì ghi rõ "baseline = 0" thay vì phần trăm cải thiện.
