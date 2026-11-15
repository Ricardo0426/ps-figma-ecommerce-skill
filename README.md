# ps-figma-ecommerce

用 Photoshop 原生素材与 Figwright 制作、续修及交付**可编辑**的 Figma 电商主副图和 A+ 套图的 ZCode 技能（skill）。

适用于：

- PS/PSD/PSB 到 Figma 的素材提取（含嵌套智能对象、AI 矢量智能对象）
- 按需求表和参考图排版、多轮局部修改
- 成图导出检查、压缩包交付及 Excel/WPS 图片回填

不用于纯前端代码实现，或没有 PS/Figma 需求的普通生图。

## 目录结构

```
ps-figma-ecommerce/
├── SKILL.md                      # 技能主文件（触发条件、核心约束、流程路由）
├── CHANGELOG.md                  # 变更记录
├── agents/openai.yaml            # Agent 展示信息（名称、简介、默认提示词）
├── references/
│   ├── photoshop-native-assets.md   # PSD/PSB 素材提取（嵌套智能对象、矢量导出）
│   ├── figma-editing.md             # Figma 新建/局部修订、恢复版式、超时恢复
│   ├── backgrounds.md               # 场景/光轨生成、清晰度和跨图变化
│   ├── delivery-and-xlsx.md         # 原尺寸导出、逐图检查、需求表插图与回填
│   └── project-contract.md          # 交付索引与素材索引的字段约定
└── scripts/
    ├── check_delivery.py            # 交付图与需求表的一致性检查
    └── replace_xlsx_images.py       # Excel/WPS 需求表图片回填
```

## 安装

将 `ps-figma-ecommerce/` 目录复制到 ZCode 技能目录（如 `~/.zcode/skills/`）即可；也可直接下载 [ps-figma-ecommerce-skill-updated.zip](./ps-figma-ecommerce-skill-updated.zip) 解压后使用。

## 许可

暂未附带许可证，默认保留所有权利。如需复用请先联系作者。
