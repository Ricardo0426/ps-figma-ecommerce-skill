# 项目索引与状态约定

开始/接手时先定位当前权威文件，不必按本示例重命名已有工程。轮次可以用日期加修订号；时间、路径、字号、尺寸、颜色和图数来自本项目，不能套用以前的项目。

## 最小目录

```text
project/
  requirements/          当前需求、参考和提取后的可读记录
  assets/native/         PS原始内容导出，保留来源
  assets/derived/        明确记录裁切/调色的派生
  revisions/<revision>/
    before.json
    delivery-index.json
    asset-index.json
    operations.jsonl
    previews/
    export/
    qa.json
    handoff.md
```

只创建当前任务需要的内容。原文件、源文档和已确认页面保持原位；使用新修订目录避免覆盖历史交付。原始图片和用户资料不应复制到 skill 目录。

## 交付索引

用户已完成并确认的示例图可以作为风格依据，但须在索引中标记为保护对象；其余画板/尺寸应各自记录继承特征与明确例外。源稿作为单独参考导入时，另设 `source_reference_group_id`，不能与可交付根组混淆。交付尺寸变化后，为同一信息重建版式时保留原组，新增 `revision/output_id`，不覆盖或拉伸源参考。

可使用已有格式；没有时建议 JSON 数组，每项至少如下：

```json
[
  {
    "output_id": "detail-material",
    "sequence": 4,
    "label": "主副图4",
    "sheet": "需求细节",
    "row": 10,
    "copy_cell": "H10",
    "feedback_cells": ["K10"],
    "width": 2250,
    "height": 3000,
    "figma_group_id": "read-from-current-session",
    "protected": false,
    "scope": "只改材质特写组合",
    "files": {"png": "export/material.png", "jpg": "export/material.jpg"}
  }
]
```

示例数字仅解释字段。主副图/A+不要求固定尺寸、数量、名称或工作表列位。`sequence` 是本轮表格图序，`output_id` 是跨轮次标识，不用顺序充当身份；Figma 的节点 ID 只能从工具读到。

## 素材索引

```json
{
  "alias": "wheel-grip-closeup",
  "source_document": "sources/product.psb",
  "source_document_id": 123,
  "source_layer_id": 456,
  "source_kind": "smartobject",
  "nested_chain": [{"layer_id": 456, "file": "embedded.psb"}],
  "native_path": "assets/native/wheel.png",
  "native_size": [4000, 4000],
  "display_path": "assets/derived/grip.png",
  "crop_box": [0, 300, 1500, 1230],
  "crop_intent": "握把、缝线和按键局部特写",
  "transforms": [],
  "instances": [{"output_id": "detail-material", "node_id": "read-from-session"}]
}
```

使用原图裁切时坐标以原图像素为单位。若有去边距、缩放、调色、原始小尺寸上采样，分别记录，不能把派生尺寸写成原生分辨率。条件允许可加 SHA256、alpha bbox 和产品像素核查。

精确还原外观时，再记录源导出矩形/画板原点、alpha bbox 偏移、已烘焙的效果/opacity/剪贴基底，以及目标节点的混合方式，避免迁移位置错误或重复合成。`source_document_id` 是运行期定位线索，重开文档后不可直接复用；保留可重新查找的源路径和画板/图层身份。

## 状态与错误记录

操作记录至少保留 `revision, output_id, tool, target_id, status, returned_ids, verification`。实际文件可用 JSONL。状态可以用 `prepared/applied/verified/failed/unknown`，不要把请求发出记为完成。

交接写明：

- 当前 Figma 文件/页面、有效交付组、隐藏历史组与原参考组。
- 哪张保护不变、哪些局部修改、从何版本恢复，是否有未纳入本轮的并发改动。
- 当前完整导出目录、总览、PNG/JPG、压缩包、工作表副本和原表同步状态。
- 哪些审计在本轮重做、哪些功能/源像素存在限制、生成素材的模型/质量/尺寸/提示词位置。
- 原文件是否保存/覆盖、旧脚本不能直接重跑的原因。

结构快照和对照图附上读取/导出时间、页面/目标组范围及对应轮次，区分中间状态与暂停时最新读取。只读取主页面或原有目标组时，不称完整文件备份；新增页面、节点与未验收内容单独说明。暂停归档的范围和停止条件见 [delivery-and-xlsx.md](delivery-and-xlsx.md)。

如果工具不可用，交接把工程变更标为未执行；本地准备文件不是远端写入证明。
