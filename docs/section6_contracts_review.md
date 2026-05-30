# Section 6 ρ ≤ 1.98 契约审查与改写建议

> 本文件解释为什么 Xia 在论文 Section 6 Conclusions 里一句话提到的 1.98 上界可能成立 —— 也就是把势函数换成 $\Phi_O(u,v) = L_n(u,v)$（线段 uv 落在最末盘 $O_n$ 里的那段长度）—— 同时整理 [scripts/verify_section6_198.py](../scripts/verify_section6_198.py) 里 S1–S6 六条 obligation 契约需要做哪些重写，才能让 agent 在 v-依赖 Φ 的新世界里有的放矢。

## 1. 直觉：为什么"换成一个长度"反而能更紧？

关键不在于 $L_n$ 这个函数有多漂亮，而在于**它跟 $|P|$ 的符号关系**完全不同。

### 1.1 老 Φ 在极端链上"帮倒忙"

旧框架（Xia 1.998 证明）的势函数大致是

$$\Phi_O^{\text{old}} = a(r_n - r_1) - \sum_i \psi(H_i, V_i).$$

那个 $-\sum \psi$ 项在极端链上累加得很大，所以 $\Phi^{\text{old}}$ 在最难的情形下**非常负**。它把 $\Upsilon = |P| - \lambda|D| + \Phi$ 往负的反方向拉，Lemma 3 只能给出

$$\Phi_{O^*}^{\text{old}} \ge -C \cdot |P_{O^*}|,$$

即"它最多坏到这个程度"。这个负号传到最终公式上是

$$\rho \le \frac{\lambda}{1 - C}, \quad C > 0.$$

C 越小越好，但只要 C > 0 就在分母吃亏。Xia 把 C 压到能给 1.998 的水平，没法再往下。

### 1.2 新 Φ = $L_n(u,v)$ 自带正号

线段 uv 落在最末盘 $O_n$ 里那段的长度有两个非常好的性质：

1. **永远 $\ge 0$**（长度嘛）。
2. **在极端链上不仅 $\ge 0$，还大概率 $\sim c \cdot |P|$**：极端链就是被几何强行拉长的链，对应 uv 方向上它必然贯穿 $O_n$ 一段不小的弦长，而这个弦长正比于 $|P|$ 量级。

所以你能期望证明的下界形式是

$$L_n(u^*, v^*) \ge B \cdot |P_{O^*}|, \quad B > 0.$$

packet 里取的 $B = 3/10$。注意符号：是 **$+B$**，不是旧框架的 $-C$。

### 1.3 这个正号传到最终公式上

由 $\Upsilon = |P| - \lambda|D| + \Phi < 0$ 与 $\Phi \ge B|P|$ 做代数：

$$|P| - \lambda|D| < -\Phi \le -B|P| \;\Rightarrow\; (1+B)|P| < \lambda|D| \;\Rightarrow\; \rho < \frac{\lambda}{1+B}.$$

**分母从 $1-C$ 变成了 $1+B$** —— 从被惩罚变成被奖励。

packet 里 $\lambda = 643/250 = 2.572$，$B = 0.3$，于是

$$\rho \le \frac{2.572}{1.3} \approx 1.9785 < 1.98 \checkmark$$

### 1.4 Insight 一句话

> **新 Φ 把"对抗项"变成了"协助项"**。$L_n$ 自带正号，而且在极端配置下大小正比于路径长度本身，所以它能在 Lemma 3 那一步给你加分而不是扣分。代价是失去了"Φ 是常数"这个简化承重墙 —— Xia 之所以说 "quite complicated" 就是因为这层柱子塌了之后所有上层结构都得重做。

思想本身是 sound 的。能不能严格证出 1.98，落到两个具体问题上：

- **能不能严格证 $L_n \ge \tfrac{3}{10}|P|$ 在所有 extremal chain 上？** 这是个纯几何不等式，看上去合理但不平凡。需要刻画 extremal chain 的几何形状，证明在那种配置下 uv 跟 $O_n$ 必然交出 $\ge 0.3|P|$ 长的一段。
- **新 Lemma 2（对所有链 Υ < 0）能不能在 v-依赖 Φ 下证出来？** 这就是 S2 / S3 / S4 / S5 那一堆事。

两件事 Xia 都没在论文里给。所以 "quite complicated" 是诚实评价。

---

## 2. 契约清单：S1–S6 当前文案与建议重写

每条给三块：**当前 packet 里的 claim 文案** / **它的问题** / **建议的新 claim 文案**。注意这些是 agent 看到的"我要证什么"的命题陈述，不是证明本身。

### S1 — 单盘基础情形

**当前** ([scripts/verify_section6_198.py:184](../scripts/verify_section6_198.py#L184))
> "The one-disk case satisfies Upsilon_O(u,v) < 0 for the chosen lambda and eta."

**评估** ✓ **基本对，无需大改**。当 n=1 时 uv 整段都在 $O_1$ 内，所以 $L_1(u,v) = |D_O(u,v)|$，Υ 化为 $|P| - (\lambda - \eta)|D|$，再用 $|P| \le (\pi/2)|D|$ 得到 $\lambda - \eta > \pi/2$ 即可。这条 agent 已经 PASS。

**建议新 claim**（仅小幅澄清）
> "For n=1, the segment u–v lies entirely inside the single disk $O_1$, so $L_1(u,v) = |D_O(u,v)|$. Show that $|P_O(u,v)| - (\lambda - \eta)|D_O(u,v)| < 0$ holds for every nondegenerate $u \ne v$, using the one-disk path bound $|P_O| \le (\pi/2)|D_O|$. With $\eta = 1$, $\lambda = 643/250$, this reduces to $\lambda - \eta > \pi/2$."

---

### S2 — 末端盘步进（**必须重写，是当前主要 blocker**）

**当前** ([scripts/verify_section6_198.py:193](../scripts/verify_section6_198.py#L193))
> "The terminal-disk induction step remains valid with Phi_eta."
>
> summary: "Show that adding/removing the terminal disk preserves the induction comparison needed for Prop 2."

**问题**
"adding/removing the terminal disk preserves the induction comparison" 在文字上还是旧框架的 **Φ-单调** 语义（$\Phi_O \le \Phi_{O_{1,n-1}}$）。对 v-依赖的 $L_n$ 这条**字面不成立**，agent 已经在 S2 cert 里把这个事实点出来。问题不是 "agent 证不出来"，是"题目本身就给错了"。

**建议新 claim**（Υ-端点形式，对应 Xia 真正的 Prop 2）
> "Prove the endpoint case of Lemma 2 directly at the Υ level for $\Phi = \eta L_n(u, \cdot)$. That is, for every chain $O$ with $n \ge 2$ and every $x \in \{a_{n-1}, b_{n-1}\}$, show
> $$\Upsilon_O(u, x) \;=\; |P_O(u, x)| - \lambda |D_O(u, x)| + \eta L_n(u, x) \;<\; 0.$$
> The agent **must not** attempt to reduce this to $\Phi_O \le \Phi_{O_{1,n-1}}$ (the Φ-monotonicity step from Xia's original v-independent framework); that reduction fails for the segment potential because adding the terminal disk can introduce $L_n(u, x) > L_{n-1}(u, x) = 0$ with no compensating decrease in $|P|$ or $|D|$. The proof must instead bound $\Upsilon_O(u, x)$ as a single three-term combination, treating $L_n(u, x)$ as a *positive* contribution that must be absorbed by the slack in $|P_O(u, x)| - \lambda |D_O(u, x)|$ at the two endpoint candidates."

---

### S3 — 阻塞拆分（**必须重写**）

**当前** ([scripts/verify_section6_198.py:202](../scripts/verify_section6_198.py#L202))
> "The obstructed split step remains valid with Phi_eta."
>
> summary: "Show that the potential term can be split or otherwise controlled when the chain is divided at an obstruction."

**问题**
旧 N3 是 $\Phi_O \le \Phi_{O^L} + \Phi_{O^R}$ —— Φ 层面的次可加性。对 v-依赖 Φ 这没意义，因为左/右子链的 "v" 已经不是原 v 而是拆分点 $p$，所以两边的 $L_n$ 不是同一个量级的东西：

- $L_n$（原）：uv 在最末盘 $O_n$ 里的弦
- 左子链的对应量：u-p 在 $O_{j+1}$ 里的弦
- 右子链的对应量：p-v 在 $O_n$ 里的弦

合并起来就不是简单加法。

**建议新 claim**（Υ 层面拆分，对应 Xia 的 Prop 3）
> "When $D_O(u, v)$ is obstructed at pivot $p \in \{a_j, b_j\}$, split the chain into $O^L = O_{1, j+1}$ (terminals $u, p$) and $O^R = O_{j+1, n}$ (terminals $p, v$). Prove the Υ-level subadditivity:
> $$\Upsilon_O(u, v) \;\le\; \Upsilon_{O^L}(u, p) + \Upsilon_{O^R}(p, v).$$
> Equivalently, after using $|D_O(u, v)| = |D_{O^L}(u, p)| + |D_{O^R}(p, v)|$ (obstruction passes through $p$) and $|P_O(u, v)| \le |P_{O^L}(u, p)| + |P_{O^R}(p, v)|$ (concatenation of paths), the obligation reduces to the *segment-potential balance*:
> $$\eta L_n(u, v) \;\le\; \eta L_{j+1}(u, p) + \eta L_n(p, v).$$
> Establish this segment inequality geometrically: $L_n(u, v)$ is the chord cut by the line $uv$ inside the terminal disk $O_n$, while $L_n(p, v)$ is the chord cut by the line $pv$ inside the same $O_n$. Since $p$ is geometrically between $u$ and $v$ along the obstructed configuration, the chord cut by $uv$ is no longer than the chord cut by $pv$ (because $pv$ is the 'inner' segment closer to $O_n$). Combine with $L_{j+1}(u, p) \ge 0$."
>
> *Note:* the inner geometry lemma here is exactly the type of non-trivial step Xia hand-waved as "quite complicated" — it is genuinely new and may require auxiliary case analysis (obstruction angle, position of $p$ inside or outside $O_n$).

---

### S4 — 终点最坏点定位（**契约改动较小，但要明确接受多种证书**）

**当前** ([scripts/verify_section6_198.py:211](../scripts/verify_section6_198.py#L211))
> "The worst terminal point contribution to Upsilon_O(u,v) is controlled despite v-dependence."

**评估** 已经是 relaxed 版的 D4，**方向是对的**，但太宽泛、不告诉 agent 具体在哪个 v 范围上做、可以用什么形式的证书。

**建议新 claim**（具体化）
> "For the non-endpoint, no-pivot, unobstructed terminal subarc $\widehat{A} \subset \partial O_n$ (i.e., v moves on this arc with no special-position simplification), establish
> $$\sup_{v \in \widehat{A}} \Upsilon_O(u, v) < 0$$
> using **any one** of the following certificate forms (all acceptable):
>
> (a) *Endpoint reduction with corrected analysis.* Show that $\Upsilon_O(u, \cdot)$ restricted to $\widehat{A}$ is convex, monotone, or otherwise has its supremum at $\partial \widehat{A}$; then verify it is negative at those endpoints (overlap with S2).
>
> (b) *Piecewise / pivot certificate.* Split $\widehat{A}$ into subarcs where the sign of $\partial \Upsilon / \partial v$ is constant (locate pivots / critical points along the arc), then verify negativity on each piece.
>
> (c) *Direct regional certificate.* Treat $\widehat{A}$ as a one-parameter region; emit a numeric/symbolic interval certificate over that arc (delegated to the `interval_region` tool).
>
> **Important:** with $\Phi = \eta L_n(u, v)$ and v moving on $\partial O_n$, the segment $[u, v] \cap O_n$ changes continuously — be explicit about whether $L_n(u, v)$ is computed using the full chord (when $u \notin O_n$) or partial chord, and which case applies on the relevant arc."

---

### S5 — 区域下降证书（**契约较小修改，工具层是大坑**）

**当前** ([scripts/verify_section6_198.py:220](../scripts/verify_section6_198.py#L220))
> "The transformation/descent expression is strictly negative on the full feasible region."

**评估** 框架对，但**没说清楚是什么表达式、什么区域**。Prop 7 的具体表达式在 v-依赖 Φ 下不是 4 个 $g_i(\alpha) < 0$，而是一个多变量函数 $\Upsilon$ 在某个多维 region $K$ 上的偏导符号。

**建议新 claim**
> "In the transformation step (Xia's Section 4 Prop 7 analog) where the chain is continuously deformed by moving $o_n$ toward $o_{n-1}$ along a parameter $X = X_{o_n}$, show that the directional derivative
> $$\frac{\partial \Upsilon_O(u, v)}{\partial X} \;=\; \frac{\partial |P_O|}{\partial X} - \lambda \frac{\partial |D_O|}{\partial X} + \eta \frac{\partial L_n(u, v)}{\partial X} \;<\; 0$$
> on the relevant feasible region. The region is parameterized by, at minimum, $(\alpha, \beta, \gamma)$ as in Xia's Section 4.2, with $v \in \widehat{A}$ giving one additional parameter (since $L_n$ depends on $v$). Total dimension: 3–4.
>
> Do not attempt to compress this to four single-variable $g_i(\alpha) < 0$ certificates — that compression depends on $\Phi$ being v-independent and fails here. Instead, produce a multivariable region certificate over the explicit feasible box $K \subset \mathbb{R}^d$ (with $d \in \{3, 4\}$) via the `interval_region` tool. The total derivative of $L_n$ with respect to $X$ must be computed explicitly, including the contribution from $\partial v / \partial X$ when $v$ is parameterized by chain geometry.
>
> **Region specification** is itself a required deliverable: list every constraint defining $K$ (e.g., $0 < \alpha < \pi$, feasibility of $v$ on $\widehat{A}$, etc.) before invoking the verifier."

---

### S6 — 极端链下界（**契约方向对，但需要把 "$L_n \ge 0.3|P|$" 这个核心几何不等式点出来**）

**当前** ([scripts/verify_section6_198.py:229](../scripts/verify_section6_198.py#L229))
> "On the extremal chain, Phi_eta >= B*|P| with the B recorded in constant_chain."

**评估** 方向对，但太抽象，没告诉 agent **B = 3/10 这个具体数字背后的几何不等式**是什么。

**建议新 claim**
> "On the extremal chain $O^*$ selected by Xia's Lemma-3 extremal procedure (aligned chain, terminals $u^*, v^*$), establish the geometric inequality
> $$L_n(u^*, v^*) \;=\; \mathcal{H}^1\bigl([u^*, v^*] \cap O_n\bigr) \;\ge\; \tfrac{3}{10} \, |P_{O^*}(u^*, v^*)|.$$
> Note this is a *positive* lower bound (not the $\Phi \ge -C|P|$ form from Xia's original 1.998 Lemma 3): the segment potential is nonnegative and on extremal chains the chord cut by the terminal line inside $O_n$ is forced to be substantial relative to the path length. This is the source of the improved $\rho \le \lambda / (1 + B)$ with $B = 3/10$ rather than the original $\rho \le \lambda / (1 - C)$ with small $C$.
>
> The proof must:
>
> 1. Characterize the extremal chain geometry (cite or restate Xia's Section 5 / Prop 8–10 reduction to aligned chains).
> 2. On the aligned chain, show that the line $u^* v^*$ enters $O_n$ at a chord of length $\ge \tfrac{3}{10} |P_{O^*}|$. This will typically use:
>    - the radius $r_n$ of $O_n$ relative to the running radii $r_1, \dots, r_{n-1}$,
>    - the angle at which $u^* v^*$ crosses the boundary of $O_n$,
>    - and a comparison between $|P_{O^*}|$ (path length, sum of arc contributions across all disks) and $L_n$ (chord in terminal disk only).
> 3. Verify the constant 3/10 is tight or near-tight (otherwise B can be made larger, improving ρ further).
>
> No part of Xia's original Lemma 3 Cauchy–Schwarz / heavy–light decomposition is directly reused — that machinery controlled the $-\sum \psi(H_i, V_i)$ negative term, which is absent here. The new proof is essentially a *chord-versus-path-length geometric inequality on aligned chains*."

---

## 3. 交叉的修订原则

把 6 条 claim 改完之后还有 3 个全局事项要同步：

1. **`ANALYTIC_CERTIFICATE_REQUIRED` 集合**（[scripts/verify_section6_198.py:83](../scripts/verify_section6_198.py#L83)）当前是 `{S1, S2, S3, S6}`。新 S3 涉及一个真·几何不等式（chord inequality），S6 涉及 chord-vs-path 比较 —— 这两条**可能也需要数值证书**辅助（不是纯解析），可以考虑把它们也允许 tool_requests。

2. **`SOURCE_STYLE_PROOF_SKELETON`**（[scripts/verify_section6_198.py:239](../scripts/verify_section6_198.py#L239)）继承自旧 proof_agent 的 6 段式。建议在新 contract 里**强调** $\Phi$ 是 v-依赖的，并在 prompt 里禁止 agent 走 "Φ-monotonicity" / "compress to univariate" 这些旧捷径。

3. **`section6_property_context()` 和 `_PROPERTY_KEYWORDS`** 里的 RAG 关键词，特别是 S2 的关键词当前包含 `"adding a disk"`, `"does not increase"`，这些都是旧框架的语言，会把 RAG 拉到错的论文段落上。**关键词也要为 v-依赖 Φ 重写**。

---

## 4. 下一步建议

**不要一次把 6 条全改下去**。理由：

- S1 已经 PASS，先动 S2 + 同步关键词，跑一遍看看 agent 在新契约下能不能推进 S2，再回头处理 S3–S6。
- S2 是当前实际 blocker；它推进了说明改契约这条路 work。
- 一次改一条便于回滚和归因。
- 如果 S2 在新契约下 agent 仍然 blocked，那意味着不仅是契约错，agent 还缺真正的证明能力 —— 这时候需要 RAG / 工具层的补救才有意义，盲改 S3–S6 没用。

执行顺序建议：

1. 改 S2 的 claim 文案 + summary 文案 + `_PROPERTY_KEYWORDS["S2"]`。
2. 重跑 `python scripts/verify_section6_198.py --do S2 --rounds 2`，看 agent 在新契约下的 cert 内容是否实质推进。
3. 根据结果决定下一步：要么继续 S3，要么先补 RAG / 工具层。

---

## 5. 本轮脚本改造记录

本轮已经把 [scripts/verify_section6_198.py](../scripts/verify_section6_198.py) 做到可以继续探索 relaxed Section 6 方案：

- 修复从当前项目目录直接运行时的 `upper_bound_agent` 导入路径问题。
- 将 S2 契约改成直接 endpoint-level 的 $\Upsilon_O(u,x)<0$，并在 prompt/review 里禁止复用旧的 $\Phi_O \le \Phi_{O_{1,n-1}}$ 单调性捷径。
- 将 S3 契约改成 $\Upsilon$ 层面的 compensated split defect，而不是 standalone 的旧 $\Phi$ 次可加性。
- 明确 S4/S5 需要处理 $v$-dependent 的 $L_n(u,v)$，S5 允许多变量 `interval_region` 证书，不再默认压成四个单变量 $g_i$。
- 明确 S6 的核心目标是正的 terminal-penetration 下界 $L_n \ge (3/10)|P|$，常数链代数本身不再算作 S6 闭合。
- 更新 RAG/task keywords，去掉 S2 的 `"adding a disk"` / `"does not increase"` 等旧框架诱导词。
- 调整 verifier gate：S1/S2 仍必须解析闭合；S3/S6 需要手工归约，但允许剩余显式不等式由 passing replayable tool certificate 覆盖；S4/S5 继续允许工具证书闭合。
- 增加 self-test，验证 `interval_region` 能闭合 obligation，以及 S3 这种“manual reduction + interval certificate”的 relaxed 闭合路径能被接受。

验证命令已通过：

```bash
python -m py_compile scripts/verify_section6_198.py
python scripts/verify_section6_198.py --self-test --no-log
```

注意：这不是一次性把 1.98 证明全部改完，也不是一次性闭合 S1-S6。它只是把脚本从旧契约验证器改成了适合继续探索 relaxed 框架的验证台。当前建议仍然是先单独推进 S2：

```bash
python scripts/verify_section6_198.py --spec artifacts/section6_198_develop_s2_packet.json --do S2 --rounds 2
```
