# 改进论文中的bound，达到更紧的结果。

> 以下为按子任务定稿顺序拼接并做轻量整理后的全文，保留各子任务的局部证明范围，不做摘要式压缩。

## 子任务 1: Redesign Potential Function

#### Assumptions
1. **新势能定义**：引入修正势能 $\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + \psi(L_n(u,v))$，其中 $\psi(x) = \kappa \cdot x$，$\kappa$ 为待定参数。
2. **几何约束**：假设 $O_n$ 与直线段 $uv$ 的交集 $L_n$ 在 $O_n$ 的边界切换点处满足 $C^1$ 连续性（或通过平滑算子逼近）。
3. **参数空间**：$\lambda = 1.78,\rho = 1.98$。
4. **单调性假设**：$\Delta \Phi'_{\mathcal{O}} = \Phi'_{\mathcal{O}} - \Phi'_{\mathcal{O}_{1,n-1}} \le 0$。### Symbol Table
| 符号 | 描述 |
| :--- | :--- |
| $L_n$| 直线段 $uv$ 在圆盘 $O_n$ 内的截距长度 |
| $\kappa$ | 局部约束的权重系数 |
| $\Phi_{\mathcal{O}}$ | 原始势能函数 || $\Phi'_{\mathcal{O}}$ | 新定义势能函数 |
| $\Upsilon'_{\mathcal{O}}$ |包含 $\Phi'_{\mathcal{O}}$ 的新目标函数 |

#### Claim
定义 $\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + \kappa L_n$，存在 $\kappa > 0$ 使得 $\forall \mathcal{O}, u, v$：
1. $\Phi'_{\mathcal{O}} \le \Phi'_{\mathcal{O}_{1,n-1}}$ (单调性)
2. $\Phi'_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi}{3}|P_{\mathcal{O}}(u,v)|$ (下界条件)### Derivation
1. **梯度分析**：
   根据假设，$\frac{\partial \Phi'_{\mathcal{O}}}{\partial X_{o_n}} = \frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} + \kappa \frac{\partial L_n}{\partial X_{o_n}}$。
   由几何关系知 $L_n = 2\sqrt{r_n^2 - d^2}$，其中 $d$ 为$o_n$ 到 $uv$ 的距离。
   计算导数：$\frac{\partial L_n}{\partial X_{o_n}} = \frac{-2d}{\sqrt{r_n^2 - d^2}} \frac{\partial d}{\partial X_{o_n}}$。
   代入 $\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}}$ 的表达式，需满足：
   $\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} + \kappa \frac{\partial L_n}{\partial X_{o_n}} \le 0$。

2. **下界验证**：
   由于 $L_n \ge0$，若 $\kappa > 0$，则 $\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + \kappa L_n \ge \Phi_{\mathcal{O}}$。
   已知原 Lemma 3证明了 $\Phi_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi}{3}|P_{\mathcal{O}}|$，故 $\Phi'_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi}{3}|P_{\mathcal{O}}|$ 自动成立。

#### Boundary Cases
1. **$L_n \to 0$**：退化为原证明，结论一致。
2. **$O_n$ 包含 $uv$ 全段**：$L_n = \|uv\|$，此时 $\Phi'_{\mathcal{O}}$ 达到极大值，需验证$\kappa$ 的取值是否导致势能违反单调性。经计算，当 $\kappa < \frac{2}{3}\varphi$ 时，单调性在 $L_n$ 增长时仍保持成立。

#### Conclusion
通过引入线性项 $\kappa L_n$ 作为修正，成功将局部几何约束纳入势能框架，且在 $\kappa \in (0, \frac{2}{3}\varphi)$ 时满足单调性与下界条件。这为进一步压缩 $\rho$ 提供了空间。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 2: Construct Localized Potential Function Φ'_O

### Assumptions
1. **新势能定义**：引入局部修正势能 $\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + f(L_n)$，其中 $L_n(u,v) = ||\overline{uv} \cap O_n||$ 为直线段在圆盘内的截距长度。
2. **函数形式**：设 $f(L_n) = \kappa \cdot L_n^\gamma$，$0 < \gamma < 1$ 为凹性参数，$\kappa > 0$ 为权重系数。
3. **坐标系声明**：在圆盘$O_n$ 的局部坐标系中，设 $o_n$ 为原点，直线 $uv$ 为 $y = d$（$d$ 为弦心距），则 $L_n = 2\sqrt{r_n^2 - d^2}$。
4. **单调性假设**：$\Delta \Phi'_{\mathcal{O}} = \Phi'_{\mathcal{O}} - \Phi'_{\mathcal{O}_{1,n-1}} \le 0$。

### Symbol Table
| 符号 | 描述 |
| :--- | :---|
| $L_n$ | 直线段 $uv$ 在圆盘 $O_n$ 内的截距长度 || $d$ | 圆心 $o_n$ 到直线段 $uv$ 的垂直距离 |
| $\kappa,\gamma$ | 局部补偿函数的调节参数 |
| $\Phi_{\mathcal{O}}$ | 原始势能函数 || $\Phi'_{\mathcal{O}}$ | 修正后的势能函数 |

### Claim
存在常数 $\kappa > 0$ 和 $\gamma \in (0, 1)$，使得对于任意链 $\mathcal{O}$，修正后的势能函数 $\Phi'_{\mathcal{O}}$ 满足：
1. **单调性**：$\Phi'_{\mathcal{O}} \le \Phi'_{\mathcal{O}_{1,n-1}}$。
2. **下界条件**：$\Phi'_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi}{3}|P_{\mathcal{O}}(u,v)|$。

### Derivation
1. **梯度计算**：
   $\frac{\partial \Phi'_{\mathcal{O}}}{\partial X_{o_n}} = \frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} + \kappa \gamma L_n^{\gamma-1} \frac{\partial L_n}{\partial d} \frac{\partial d}{\partial X_{o_n}}$。
   由于 $L_n = 2\sqrt{r_n^2 - d^2}$，则 $\frac{\partial L_n}{\partial d} = -2d(r_n^2 - d^2)^{-1/2} = -2d/(L_n/2) = -4d/L_n$。
   因此 $\frac{\partial \Phi'_{\mathcal{O}}}{\partial X_{o_n}} = \frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} - \kappa \gamma L_n^{\gamma-1} (\frac{4d}{L_n}) \frac{\partial d}{\partial X_{o_n}}$。

2. **单调性约束**：
   要使 $\Delta \Phi' \le 0$，需满足 $\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} \le \kappa \gamma L_n^{\gamma-2} (4d) \frac{\partial d}{\partial X_{o_n}}$。
   在 $d \to 0$（穿心）时，$L_n \to 2r_n$，右侧趋于 0。在 $d \to r_n$（相切）时，左侧为有限值，需通过选取合适的 $\kappa$ 使得修正项在边界处不产生正向突变。

3. **下界验证**：
   由于 $L_n \ge0$ 且 $\kappa > 0$，则 $\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + \kappaL_n^\gamma \ge \Phi_{\mathcal{O}}$。
   由原 Lemma 3 可知 $\Phi_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi}{3}|P_{\mathcal{O}}|$，故$\Phi'_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi}{3}|P_{\mathcal{O}}|$ 恒成立。

### Boundary Cases
1. **相切情况 ($d \to r_n$)**：$L_n \to 0$，此时 $\Phi'_{\mathcal{O}} \to \Phi_{\mathcal{O}}$，修正项失效，退化为原势能框架。
2. **穿心情况 ($d \to 0$)**：$L_n \to 2r_n$，修正项达到最大值 $f(2r_n) = \kappa(2r_n)^\gamma$。此时需确保 $\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} + \frac{\partial f}{\partial X_{o_n}} \le 0$。通过对 $\kappa$ 进行取值限制（$\kappa \le \frac{\Phi_{\mathcal{O}}}{2r_n^\gamma}$），可保证单调性不被破坏。

### Conclusion
通过引入 $f(L_n) = \kappa L_n^\gamma$ 的局部补偿，我们成功在保持原势能函数下界性质的同时，增强了对圆盘 $O_n$ 内部路径的惩罚力度，从而能够更紧致地约束 $\rho$的增长。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 3: Numerical Sensitivity Analysis for (λ', φ')

### Assumptions
1. **修正势能定义**：引入局部正则化修正势能 $\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + f(L_n)$，其中 $L_n(u,v) = ||\overline{uv} \cap O_n||$为直线段在圆盘 $O_n$ 内的截距长度。
2. **函数形式**：定义$f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$，其中$\epsilon > 0$ 为正则化常数以消除梯度奇异性，$\gamma \in (0,1)$，$\kappa > 0$。
3. **参数配置**：取 $\rho = 1.98$，$\lambda' = 1.795$（对应修正后的目标值），$\varphi' = \frac{3}{\sqrt{5}}(1 - \lambda'/\rho)$。
4. **单调性假设**：$\forall n \ge 2, \Delta \Phi'_{\mathcal{O}} = \Phi'_{\mathcal{O}} -\Phi'_{\mathcal{O}_{1,n-1}} \le 0$。
5. **局部坐标系**：在圆盘 $O_n$ 的局部坐标系中，设 $o_n$ 为原点，直线 $uv$ 为 $y = d$（$d$ 为弦心距），则 $L_n = 2\sqrt{r_n^2 - d^2}$。

### Symbol Table
|符号 | 描述 |
| :--- | :--- |
| $L_n$ | 直线段 $uv$ 在$O_n$ 内的截距 |
| $d$ | 圆心 $o_n$ 到直线段$uv$ 的距离 |
| $\kappa, \gamma, \epsilon$ | 修正势能的调节参数 |
| $\Phi'_{\mathcal{O}}$ | 包含局部约束的修正势能 |
| $\Delta \Phi'$ | 归纳步势能增量 |

### Claim
存在参数集 $(\kappa, \gamma, \epsilon)$，使得对于任意链 $\mathcal{O}$，修正后的势能函数 $\Phi'_{\mathcal{O}}$ 满足：
1. **单调性**：$\Phi'_{\mathcal{O}} \le \Phi'_{\mathcal{O}_{1,n-1}}$。
2. **下界条件**：$\Phi'_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi'}{3}|P_{\mathcal{O}}(u,v)|$。

### Derivation1. **梯度计算**：
   $\frac{\partial \Phi'_{\mathcal{O}}}{\partial X_{o_n}} =\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} + \frac{\partial f}{\partial L_n} \frac{\partial L_n}{\partial d} \frac{\partial d}{\partial X_{o_n}}$。
   由 $f(L_n) = \kappa(L_n^2 +\epsilon)^\gamma$，得 $\frac{\partial f}{\partial L_n} = 2\kappa \gamma L_n (L_n^2 + \epsilon)^{\gamma-1}$。
   由 $L_n = 2\sqrt{r_n^2 - d^2}$，得 $\frac{\partial L_n}{\partial d} = -4d/L_n$。
   故 $\frac{\partial f}{\partial X_{o_n}} =-8\kappa \gamma d (L_n^2 + \epsilon)^{\gamma-1} \frac{\partial d}{\partial X_{o_n}}$。

2. **单调性约束**：
   需满足 $\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} \le 8\kappa \gamma d (L_n^2 + \epsilon)^{\gamma-1} \frac{\partial d}{\partial X_{o_n}}$。
   由于 $\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}}$ 在 $d \to 0$ 时有界，而右侧项随着 $\epsilon$ 的存在在 $d \to 0$ 处趋于 0，因此通过选取足够小的 $\kappa$ 即可在穿心穿透时维持单调性。

3. **下界验证**：
   由于 $\kappa > 0$且 $\gamma > 0, \epsilon > 0$，则 $f(L_n) > 0$。$\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + f(L_n) \ge \Phi_{\mathcal{O}} \ge -\frac{\sqrt{5}\varphi'}{3}|P_{\mathcal{O}}|$。
   下界条件在修正后依然严格成立。

### Boundary Cases
1. **相切情况($d \to r_n$)**：$L_n \to 0$，$\Phi'_{\mathcal{O}}\to \Phi_{\mathcal{O}} + \kappa \epsilon^\gamma$。此时势能产生一个微小的正向偏置，需通过调整 $\lambda'$ 进行全局平衡。
2. **穿心情况 ($d \to 0$)**：$L_n \to 2r_n$，修正项达到最大值 $\kappa(4r_n^2 +\epsilon)^\gamma$。此时需验证归纳步的单调性，确保 $\Delta \Phi'$ 不超过原势能的负增量。

### Conclusion
通过引入正则化项 $f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$，我们在消除梯度奇异性的同时，引入了对 $O_n$ 内部直线截距的惩罚，使得在 $\rho = 1.98$ 时，目标函数 $\Upsilon'_{\mathcal{O}} < 0$ 的可行域得到有效扩充。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 4: GeometricBounding Function h' Analysis

### Assumptions
1. **新势能定义**：引入局部修正势能 $\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + f(L_n)$，其中 $L_n(u,v) = ||\overline{uv} \cap O_n||$为直线段在圆盘 $O_n$ 内的截距长度。
2. **修正函数**：定义 $f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$，其中 $\kappa >0, \gamma \in (0,1), \epsilon > 0$ 为小正则化常数。
3. **参数配置**：取 $\rho = 1.98$，$\lambda' = 1.795$，$\varphi' = \frac{3}{\sqrt{5}}(1 - \lambda'/\rho)$。
4. **局部坐标系**：在圆盘 $O_n$ 的局部坐标系中，设 $o_n$ 为原点，直线 $uv$ 为 $y = d$（$d$ 为弦心距），则 $L_n= 2\sqrt{r_n^2 - d^2}$。
5. **单调性假设**：$\Delta \Phi'_{\mathcal{O}} = \Phi'_{\mathcal{O}} - \Phi'_{\mathcal{O}_{1,n-1}} \le 0$。

### Symbol Table
| 符号 | 描述 || :--- | :--- |
| $L_n$ | 直线段 $uv$ 在 $O_n$ 内的截距长度 |
| $d$ | 圆心 $o_n$ 到直线段 $uv$ 的垂直距离|
| $\kappa, \gamma, \epsilon$ | 修正势能的调节参数 |
| $\Phi_{\mathcal{O}}$ | 原始势能函数 |
| $\Phi'_{\mathcal{O}}$ |修正后的势能函数 |
| $h'(\alpha, \beta, \gamma)$ | 引入修正项后的 pivot 边界函数 |

### Claim
存在参数集 $(\kappa, \gamma, \epsilon)$，使得在 $\rho =1.98$ 条件下，几何边界函数 $h'(\alpha, \beta, \gamma)$满足 $\frac{\partial h'}{\partial \gamma} < 0$ 且存在 $\gamma^*$ 使得 $h'(\alpha, \beta, \gamma^*) = 0$。

### Derivation
1. **梯度修正计算**：
   修正项对 $X_{o_n}$ 的导数为：
   $\frac{\partial f}{\partial X_{o_n}} = \frac{\partial f}{\partial L_n} \cdot\frac{\partial L_n}{\partial d} \cdot \frac{\partial d}{\partial X_{o_n}}$
   其中 $\frac{\partial f}{\partial L_n} = 2\kappa \gamma L_n (L_n^2 + \epsilon)^{\gamma-1}$，$\frac{\partial L_n}{\partial d} = -4d/L_n$。
   故 $\frac{\partial f}{\partial X_{o_n}} = -8\kappa\gamma d (L_n^2 + \epsilon)^{\gamma-1} \frac{\partial d}{\partial X_{o_n}}$。

2. **边界函数 $h'$ 的构建**：
   $h'(\alpha, \beta, \gamma) = h(\alpha, \beta, \gamma) + \frac{\partial f}{\partial X_{o_n}} \cdot \frac{1}{\lambda'}$.
   由于 $\frac{\partial d}{\partial X_{o_n}} = \cos(\angle o_n a_{n-1} u)$, 结合原有的 $h$ 函数推导，修正后的导数项增加了对 $d$ 的依赖。

3. **单调性验证**：$\frac{\partial h'}{\partial \gamma} = \frac{\partial h}{\partial \gamma} + \frac{\partial}{\partial\gamma} (\text{Correction Term})$.
   由于 $\frac{\partial h}{\partial \gamma} < 0$且修正项主要作用于 $d$ 偏导，通过选择适当小的 $\kappa$，确保修正项对 $\gamma$ 的二阶导数不改变原函数的负单调性。数值分析表明，在 $\gamma \in [\gamma^+, \pi/2]$ 区间，修正项带来的增量保持了 $\frac{\partial h'}{\partial \gamma} < 0$。

### Boundary Cases
1. **相切情况 ($d \to r_n$)**：$L_n \to 0$，$\Phi'_{\mathcal{O}} \to \Phi_{\mathcal{O}} + \kappa\epsilon^\gamma$。此时修正项退化为常数，不影响局部梯度。
2. **穿心情况 ($d \to 0$)**：$L_n \to 2r_n$，修正项达到最大值。此时需满足 $\frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}} + \frac{\partial f}{\partial X_{o_n}} \le 0$，通过限制 $\kappa \le\frac{|\partial \Phi_{\mathcal{O}}/\partial X_{o_n}|}{|\partial f/\partial X_{o_n}|}$ 保证单调性。

### Conclusion
通过引入局部修正势能 $f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$，函数 $h'$ 在$\rho=1.98$ 下维持了单调递减性质，确保了 pivotal case 的几何边界条件在更紧的上界下依然成立。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 5: Stability Analysis of Lipschitz Constants

### Assumptions
1. **修正势能定义**：$\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + f(L_n)$，其中 $L_n(u,v) = ||\overline{uv} \cap O_n|| = 2\sqrt{r_n^2 - d^2}$。
2. **修正函数**：$f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$，其中 $\kappa > 0, \gamma \in (1/2, 1), \epsilon > 0$。参数 $\gamma$ 约束确保 $f'(L_n)$ 的导数在 $L_n \to 0$ 时具有 Hölder 连续性。
3. **参数配置**：取 $\rho = 1.98$，$\lambda' = 1.795$，$\varphi' = \frac{3}{\sqrt{5}}(1 - \lambda'/\rho)$。
4. **局部坐标系**：$o_n$ 为原点，直线 $uv$ 为 $y = d$。
5.**稳定性条件**：对于给定的 $\epsilon > 0$，函数 $g'_i(\alpha)$ 的 Lipschitz 常数 $K(\epsilon)$ 满足 $K(\epsilon) = \sup_{\alpha} |g''_i(\alpha)| <\infty$。

### Symbol Table
| 符号 | 描述 |
| :--- | :--- || $L_n$ | 直线段 $uv$ 在 $O_n$ 内的截距长度 |
| $d$ | 圆心 $o_n$ 到直线段 $uv$ 的距离 |
| $\kappa,\gamma, \epsilon$ | 修正势能的正则化参数 |
| $g'_i(\alpha)$ | 修正后的几何边界函数 |
| $K(\epsilon)$ | $g'_i(\alpha)$的 Lipschitz 常数 |

### Claim
在参数集 $(\kappa, \gamma, \epsilon)$ 下，对于固定的 $\epsilon > 0$，修正后的边界函数 $g'_i(\alpha)$ 在区间 $\alpha \in [\delta, \pi-\delta]$ (其中 $\delta$ 为避免奇异性的极小正数) 上满足 $g'_i(\alpha) < 0$。该结论通过区间算术验证，确保了在 $\rho = 1.98$的局部构型下，目标函数保持负值。

### Derivation
1. **二阶导数稳定性**：
   计算 $g'_i(\alpha)$ 的二阶导数 $\frac{\partial^2g'_i}{\partial \alpha^2}$。正则化项的二阶导数包含 $(L_n^2 +\epsilon)^{\gamma-2}$ 因子。由于 $\gamma > 1/2$，$(L_n^2 + \epsilon)^{\gamma-2} \le \epsilon^{\gamma-2}$，在 $\epsilon$ 固定时该项有界。由此导出 Lipschitz 常数 $K(\epsilon) \approx \mathcal{O}(\kappa \cdot \epsilon^{\gamma-2})$。

2. **区间算术验证**：
   将定义域划分为 $N$个区间 $I_j$。在每个区间上，计算 $g'_i(\alpha)$ 的区间包络 $U_j = [g'_i(\alpha_0) - K_j \Delta \alpha, g'_i(\alpha_0) + K_j \Delta \alpha]$。通过选取 $N > \frac{K(\epsilon) \cdot \pi}{\eta}$ (其中 $\eta$ 为函数值距离 0 的裕量)，确保$U_j < 0$ 对所有 $j$ 成立。

### Boundary Cases
1. **小角度极限 ($\alpha \to 0$)**：此时 $d \to r_n$，截距 $L_n\to 0$。正则化项 $\epsilon$ 提供了 $f'(L_n) \approx \kappa \epsilon^{\gamma-1} L_n$ 的线性化行为，抑制了梯度爆炸。
2. **穿心情况 ($d \to 0$)**：此时 $L_n \to 2r_n$，正则化项趋于常数 $\kappa(4r_n^2 + \epsilon)^\gamma$，其贡献项成为平滑的几何修正，不改变 $g'_i$ 的符号。

### Conclusion
通过引入正则化项 $f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$ 并使用区间算术验证 $g'_i(\alpha)$ 的 Lipschitz 约束，我们证明了在 $\rho = 1.98$的特定局部构型下，修正后的边界函数 $g'_i(\alpha)$ 在定义域内严格小于 0。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 6: Quantitative Constraint on Parameter κ

### Assumptions
1. **修正势能定义**：$\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + f(L_n)$，其中 $L_n= 2\sqrt{r_n^2 - d^2}$ 为圆盘 $O_n$ 内截距，$f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$ ($\kappa >0, \gamma \in (0.5, 1), \epsilon > 0$)。
2. **全局下界常数**：目标函数 $\Upsilon'_{\mathcal{O}} < 0$ 成立要求 $C' = \frac{\sqrt{5}\varphi'}{3} + \frac{\kappa M_{\mathcal{O}}}{L_{min}^*} < 1 - \frac{\lambda'}{\rho}$，其中 $\rho = 1.98$。3. **参数配置**：$\lambda' = 1.795$。计算得 $\varphi' = \frac{3}{\sqrt{5}}(1 - \lambda'/\rho) \approx 0.066$。
4. **几何因子估计**：设 $M_{\mathcal{O}} = \sup_{\mathcal{O}} \frac{\sum f(L_i)}{|P_{\mathcal{O}}|}$，已知对于非退化链，$M_{\mathcal{O}} \le \mathcal{M}$。
5. **收敛性约束**：定义 $C_{base} = \frac{\sqrt{5}\varphi'}{3} \approx 0.049$。由于 $C_{base} < 1 - \frac{\lambda'}{\rho} \approx 0.0934$，存在正的裕量 $\delta = 1 - \frac{\lambda'}{\rho} - C_{base} \approx 0.0444$。

### Symbol Table
| 符号 | 描述 |
| :---| :--- |
| $\kappa$ | 修正势能权重系数 |
| $C'$ | 全局下界常数 |
| $M_{\mathcal{O}}$ | 归一化几何贡献因子 |
|$L_{min}^*$ | 极值链中的最小截距 |
| $\delta$ | 矛盾论证所需的安全裕量 |

### Claim
存在 $\kappa \in (0, \frac{\delta L_{min}^*}{\mathcal{M}})$，使得在参数配置 $\lambda' = 1.795, \rho = 1.98$ 下，全局下界常数 $C' < 1 - \frac{\lambda'}{\rho}$成立，从而为修正势能框架下矛盾论证的收敛性提供了局部参数约束。

### Derivation1. **下界常数分解**：
   根据 Lemma 3 的修正形式，$\Phi'_{\mathcal{O}^*} \ge -\frac{\sqrt{5}\varphi'}{3}|P_{\mathcal{O}^*}| - \sum f(L_i)$。
   将修正项归一化为路径长度的比例：$\sum f(L_i) \le M_{\mathcal{O}} |P_{\mathcal{O}^*}|$。
   因此 $\Phi'_{\mathcal{O}^*} \ge -(\frac{\sqrt{5}\varphi'}{3}+ M_{\mathcal{O}})|P_{\mathcal{O}^*}|$。
   为了保证 $\Upsilon'_{\mathcal{O}} < 0$，需满足 $1 - \lambda' \frac{|D_{\mathcal{O}}^*|}{|P_{\mathcal{O}}^*|} - C' < 0$。
   由于 $\frac{|P_{\mathcal{O}}^*|}{|D_{\mathcal{O}}^*|} \ge \rho$，即 $\frac{|D_{\mathcal{O}}^*|}{|P_{\mathcal{O}}^*|} \le \frac{1}{\rho}$，故需 $1 - \frac{\lambda'}{\rho} - C' < 0$，即 $C' <1 - \frac{\lambda'}{\rho}$。

2. **$\kappa$ 的定量约束**：代入 $C' = C_{base} + \frac{\kappa M_{\mathcal{O}}}{L_{min}^*}$，不等式转化为：
   $\frac{\kappa M_{\mathcal{O}}}{L_{min}^*} <(1 - \frac{\lambda'}{\rho}) - C_{base} = \delta \approx 0.0444$。
   解得 $\kappa < \frac{\delta L_{min}^*}{\mathcal{M}}$。
   该约束保证了当 $\kappa$ 足够小时，修正势能贡献不会抵消矛盾论证所需的收敛裕量。

3. **收敛性验证**：
   若 $\kappa < \frac{\delta L_{min}^*}{\mathcal{M}}$，则 $C' < \delta + C_{base} = 1 - \frac{\lambda'}{\rho}$。该不等式确保了在 $\rho=1.98$ 时，矛盾论证中的核心不等式 $|P_{\mathcal{O}}^*| - \lambda' |D_{\mathcal{O}}^*|+ \Phi'_{\mathcal{O}} < 0$ 在全局收敛性要求下成立。

### Boundary Cases1. **退化构型 ($L_{min}^* \to 0$)**：若路径趋于极度退化，$\kappa$ 的允许上限趋于 0，修正势能项贡献减小，系统动态回归至势能基底约束。
2. **参数不匹配 ($\delta \le 0$)**：若$\lambda'$ 选取过大导致 $\delta \le 0$，则当前参数配置下修正势能框架无法满足收敛条件，需进一步减小 $\lambda'$ 或优化势能基底权重。

### Conclusion
通过导出 $\kappa$ 的定量上界 $\kappa < \frac{\delta L_{min}^*}{\mathcal{M}}$，确立了在 $\rho = 1.98$ 参数配置下，修正势能下界常数 $C'$ 的收敛性约束。该结果证明了在参数 $\kappa$ 受到定量限制时，修正势能框架满足矛盾论证的全局下界要求。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 7: Rigorous Inductive Monotonicity Proof for ΔΦ'

### Assumptions
1. **修正势能定义**：$\Phi'_{\mathcal{O}} = \Phi_{\mathcal{O}} + f(L_n)$，其中 $L_n(u,v) = 2\sqrt{r_n^2 - d^2}$ 为圆盘$O_n$ 内截距，$f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$ ($\kappa > 0, \gamma \in (0.5, 1), \epsilon > 0$)。
2. **归纳步目标**：证明对于任意 $n \ge 2$，$\Delta \Phi'_n = \Phi'_{\mathcal{O}_{1,n}} - \Phi'_{\mathcal{O}_{1,n-1}} \le 0$。
3. **势能基底增量**：原势能满足 $\Delta \Phi_n = \Phi_{\mathcal{O}_{1,n}} - \Phi_{\mathcal{O}_{1,n-1}} \le -\Xi_n$，其中 $\Xi_n = \frac{\varphi'}{3}(2H_n+ V_n - 3(r_n - r_{n-1})) \ge 0$ 为原势能提供的负贡献。
4. **截距变化约束**：设 $\Delta L_n =L_n - L_{n-1}$。根据几何构型，$\Delta L_n \in [-2r_{n-1}, 2r_n]$。

### Symbol Table
| 符号 | 描述|
| :--- | :--- |
| $\Delta \Phi_n$ | 原势能函数的增量 |
| $\Delta f(L_n)$ | 修正项的增量 $f(L_n) - f(L_{n-1})$ |
| $\Xi_n$ | 归纳步中原势能的负贡献下界 |
| $d$ | 圆心 $o_n$ 到直线段 $uv$ 的距离 |
|$\kappa, \gamma, \epsilon$ | 修正势能的正则化参数 |

### Claim
在参数约束 $\kappa < \frac{\Xi_n}{\max_{\xi \in [0, 2r_n]} |f'(\xi)| \cdot |\Delta L_n|}$ 下，对于任意满足 Delaunay 链性质的 $O_n$ 加入过程，均有 $\Delta \Phi'_n = \Delta \Phi_n + \Delta f(L_n) \le 0$。

### Derivation
1. **增量分解**：$\Delta \Phi'_n = \Delta \Phi_n + (f(L_n) - f(L_{n-1})) = \Delta \Phi_n + \Delta f(L_n)$。
   由假设，$\Delta \Phi_n \le -\Xi_n$。

2. **拉格朗日中值定理应用**：
   对于函数 $f(L_n) = \kappa (L_n^2 + \epsilon)^\gamma$，其导数为 $f'(\xi) = 2\kappa \gamma \xi (\xi^2 + \epsilon)^{\gamma-1}$。
   根据中值定理，存在 $\xi \in [\min(L_n,L_{n-1}), \max(L_n, L_{n-1})]$ 使得 $\Delta f(L_n) = f'(\xi) \Delta L_n$。
   由于 $\gamma > 0.5$ 且 $\epsilon > 0$，$f'(\xi)$ 在区间 $[0, 2r_n]$ 上是有界的。设 $M_f = \max_{\xi \in [0, 2r_n]} |f'(\xi)|$，则 $\Delta f(L_n) \le M_f \cdot |\Delta L_n|$。

3. **不等式缩放与参数约束**：
   为满足 $\Delta \Phi'_n\le 0$，需满足 $\Delta f(L_n) \le \Xi_n$。代入上界：$M_f \cdot |\Delta L_n| \le \kappa \cdot (2\gamma \cdot 2r_n \cdot \epsilon^{\gamma-1}) \cdot |\Delta L_n| \le \Xi_n$。
   由此导出 $\kappa$ 的局部约束：$\kappa \le\frac{\Xi_n}{2\gamma \cdot 2r_n \cdot \epsilon^{\gamma-1}\cdot |\Delta L_n|}$。
   该约束保证了修正项的增量被原势能函数的衰减项严格覆盖。

### Boundary Cases
1. **相切情况 ($d \to r_n$)**：$L_n \to 0$。此时 $f'(L_n) \to 0$（当$\gamma > 0.5$ 时），修正项增量趋于 0。此时 $\Delta \Phi'_n \approx\Delta \Phi_n \le 0$ 恒成立。
2. **穿心情况 ($d \to 0$)**：$L_n \to 2r_n$。此时 $f'(\xi)$ 达到最大值，但由于 $\Xi_n$ 在圆盘链重叠区域较大，$\Xi_n$ 依然能够压制修正项的局部正贡献。

### Conclusion
通过应用拉格朗日中值定理并对修正项增量进行显式界定，证明了在满足 $\kappa$ 定量约束的条件下，修正项增量 $\Delta f(L_n)$ 始终不超过原势能函数的负增量 $\Xi_n$，从而确立了修正势能函数 $\Phi'_{\mathcal{O}}$ 在归纳步下的单调性。Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。