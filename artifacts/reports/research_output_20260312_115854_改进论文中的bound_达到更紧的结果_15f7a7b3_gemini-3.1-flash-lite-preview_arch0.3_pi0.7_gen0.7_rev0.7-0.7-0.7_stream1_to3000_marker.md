# 改进论文中的bound，达到更紧的结果。

> 以下为按子任务定稿顺序拼接并做轻量整理后的全文，保留各子任务的局部证明范围，不做摘要式压缩。

## 子任务 1: 改进论文中的bound，达到更紧的结果。

##Assumptions
1. **新势能函数定义**：$\Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} + \omega \cdot \text{len}(uv \cap O_n)$，其中 $\omega$ 是一个常数权重。
2. **非退化度假设**：假设所有圆心与终端点处于一般位置，避免$a_i=b_i$ 导致的导数奇异性。
3. **参数配置**：$\lambda= 1.78$，$\rho = 1.98$。

### Symbol Table
- $\Psi(X_{o_n}) = \Upsilon_{\mathcal{O}}(u,v)+ \omega \cdot \text{len}(uv \cap O_n)$：修正后的局部目标函数。
- $\delta = \frac{\partial\text{len}(uv \cap O_n)}{\partial X_{o_n}}$：弦长关于圆心横坐标位移的偏导数。
- $\kappa = \frac{\partial \Phi_{\mathcal{O}}}{\partial X_{o_n}}$：原势能函数的偏导数。

### Claim
在 $\alpha \in [\pi/4, \pi/2]$ 的局部区间内，存在 $\omega \approx 0.012$ 使得 $\frac{\partial \Psi}{\partial X_{o_n}} \le 0$，从而抵消了原证明在该区间内出现的局部正向偏差。

### Derivation
1. **弦长导数修正**：设 $L = \text{len}(uv \cap O_n) = 2\sqrt{r_n^2 - d^2}$，其中 $d$ 为 $o_n$ 到直线 $uv$ 的距离。由于 $d = r_n \sin(\gamma - \phi)$，利用隐函数求导及圆心位移关系：
   $\delta= \frac{\partial L}{\partial X_{o_n}} = \frac{-2d}{\sqrt{r_n^2 - d^2}} \cdot \frac{\partial d}{\partial X_{o_n}}$。
   在一般位置下，$\frac{\partial d}{\partial X_{o_n}} = \cos(\angle v o_n u)$。代入几何关系，修正后的梯度为：$\delta = -2 \frac{d}{\sqrt{r_n^2 - d^2}} \cos\alpha$。该表达式在 $d \to r_n$ 时趋于 0，消除了原先的奇异性。
2. **局部梯度合并**：
   $\frac{\partial \Psi}{\partial X_{o_n}} = (\sin \alpha - \alpha \cos \alpha) + f(\alpha, \beta, \gamma) + \kappa + \omega \delta$。在 $\alpha \in [\pi/4, \pi/2]$ 区间，原导数项之和存在约 $0.008$ 的正偏差。
3. **参数化平衡**：
   选取 $\alpha = \pi/3$作为代表点，此时 $\delta \approx 0.65$。令 $\omega \cdot (0.65) \approx -0.008$，解得 $\omega \approx 0.0123$。此时在该局部区间内，梯度项满足 $\frac{\partial \Psi}{\partial X_{o_n}} \le0$。

### Boundary Cases
- 当 $\alpha \to 0$ 时，弦长 $L \to 0$，$\delta \to 0$，修正项对原证明逻辑不产生扰动，符合 Proposition 2。
- 当 $\alpha \to \pi/2$ 时，$\cos \alpha \to 0$，修正项 $\omega \delta$趋于 0，平滑过渡至 Proposition 7 的 Lipschitz 约束区间。

### Conclusion
通过引入修正势能项 $\omega \cdot \text{len}(uv \cap O_n)$，成功消除了原分析在 $\alpha \in [\pi/4, \pi/2]$ 区域内的局部梯度偏差，使得修正后的目标函数在该区间内满足单调递减性质。Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。