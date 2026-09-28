# 盛唐天工 · 认可的角色卡画风（2026-09-19）

用户看过一男一女两张角色卡后确认"可以这个"。以后的角色卡、状态卡、关键帧，画风都以本文件为准。

- 示例图：`final/card_chengchumo.png`（程处默，唐代年轻校尉）、`final/card_wife.png`（老婆，沈行舟穿越前的妻子）
- 生成脚本：`gen_cards_mf.py`（模型 gpt-image-2，经 PhanRouter；16:9；只挂一张风格参考图）
- 风格来源：参考视频《一表千金》（小云雀制作），截图在 `style-ref/`；以及用户最初给的风格说明："偏动漫造型的美型半写实 3D，乙女游戏过场动画感"

## 1. 卡面结构（原文）

```
A character design card on a soft light-grey gradient background. Left 40%: a large bust portrait at a three-quarter angle, lit
with a soft key light and a warm rim light with gentle bloom. Right 60%: the same character full body, three times side by side
at the same scale with the feet on one baseline - front view, side view in profile, back view - in a relaxed neutral standing
pose under soft even light. The same single character, outfit and colours in every view.
```

## 2. 风格块（原文）

```
Style: beautified semi-realistic 3D CG in the look of a 3D otome-game cutscene, rendered like Image 1: an idol-like beautified
face with smooth luminous porcelain skin and a soft sheen, moderately enlarged expressive eyes with clear irises and soft
catchlights, a small refined nose, soft lips, hair in large soft clumps with fine strands catching the light; fabrics, leather
and metal detailed but clean and softly lit; no outlines. Not a flat cartoon, not a plain 3D game model, not a photograph.
```

## 3. 风格参考图的说明（改进版）

示例图生成时只写了 "never its person, face, hair colour, jewellery, clothes or background"，结果老婆的发型被截图里女演员的长卷发、刘海带偏了。以后用这一版：

```
Image 1 is a STYLE reference from the target drama: copy ONLY its rendering - skin, eye and hair treatment, lighting and depth of
field. Copy nothing else: not its person, face shape, features, hairstyle, hair length, hair colour, makeup colours, jewellery,
clothes, pose or background.
```

## 4. 约束（原文）

```
No text, no labels, no numbers, no colour swatches, no arrows, no logo, no watermark, no border lines.
```

卡上不能有字，因为参考卡上的字会被带进画面。社区模板里的色号、标注都不要加。

## 5. 角色描述的写法（示例）

- 程处默：`a young Tang-dynasty cavalry captain about twenty years old, tall and well built, handsome with sharp straight brows and a proud, slightly disdainful gaze; ... polished steel helmet with a red plume; bright lamellar Tang armour with round chest plates over a deep crimson robe ... Ancient costume, but clean, handsome and beautified - not gritty, not muddy.`
- 老婆：`a modern Chinese woman in her early thirties, the lead's wife, gentle and intellectual: a soft oval face with a warm calm smile, neat shoulder-length dark brown hair tucked behind one ear, natural light makeup; a cream knitted cardigan over a pale blue blouse, a long beige skirt and simple flat shoes.`

## 6. 以前走偏的方向（不要再用）

- "simplified materials, clean light"，以及去掉 cinematic、depth of field、bokeh：会把画面推成扁平的 3D 模型感。参考视频本身就有逆光、光晕和浅景深。
- 平光、无投影、小脸全身的三视图设定图：失去美型感。
- 只挂状态卡（全身、脸小）做身份参考：每张的脸都不一样。有固定人物时，要加面孔图和转面图。
