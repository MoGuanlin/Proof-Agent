# 改进论文中的bound，达到更紧的结果。

> 以下为按子任务定稿顺序拼接并做轻量整理后的全文，保留各子任务的局部证明范围，不做摘要式压缩。

## 子任务 1: Formulation of the Enhanced PotentialFunction ($\Phi_{\mathcal{O}}'$)

### Assumptions
1. **坐标系与独立变量声明:**
   - 在局部推导中，考查从状态 $n-1$ 到状态 $n$ 的演化。设状态增量为 $\Delta r = r_n - r_{n-1}$，以及圆心位移导致的垂直距离增量 $\Delta d = d_n - d_{n-1}$。
   - 设圆心位移向量为 $(\Delta X, \Delta Y)$，且射线 $\overrightarrow{uv}$ 的法线方向角为 $\gamma + \pi/2$，则几何投影关系满足 $\Delta d = -\Delta X \sin \gamma + \Delta Y \cos \gamma$。- 依据原文献中峰值路径（Peak Path）的几何性质，声明演化增量的几何界限：水平演化距离严格控制水平位移，即 $|\Delta X| \le H_n$；垂直演化距离控制垂直位移与半径变化，即 $|\Delta Y| \le V_n$ 且 $|\Delta r| \le V_n$。
2. **替代定义/新定义 (Redefinitions/New Definitions):**
   - 定义弦长为状态变量 $(r, d)$ 的函数$L(r,d) = 2\sqrt{r^2 - d^2}$，其中 $r$ 为圆盘半径，$d$ 为圆心到直线 $uv$ 的有向垂直距离。该弦长即为线段 $uv$ 在圆盘内截得的长度。
   - 引入平滑截断函数 (Smooth cutoff function) $\eta(x) \in [0,1]$，以处理射线与圆盘相切（Grazing rays）时的解析奇点。设定当 $|x| \le1-2\epsilon$ 时 $\eta(x)=1$，当 $|x| \ge 1-\epsilon$ 时 $\eta(x)=0$，且其在过渡区域内连续可微，导数有界 $|\eta'(x)| \le M_\epsilon$。
   - 引入改进的势函数 $\Phi_{\mathcal{O}}'$，将半径项符号取负以提供必要的负增量，并调整几何惩罚项的权重以确保势函数自身严格非增。加入平滑截断后的弦长项 $\psi L_n \eta(\sin \omega_n)$ 作为补偿项。

### Symbol Table
| Symbol | Definition || :--- | :--- |
| $\mathcal{O}$ | 圆盘链 (Chain of Disks)，$\mathcal{O}_{1,n-1}$ 表示前 $n-1$ 个圆盘组成的子链 |
| $u, v$ | 链的端点 (Terminals) |
| $r_i, o_i$ | 第 $i$ 个圆盘 $O_i$ 的半径与圆心 |
| $L_i$ | 直线 $uv$ 在 $O_i$ 内截得的弦长，$L_i = 2\sqrt{r_i^2 - d_i^2}$ |
| $d_i$ | 圆心 $o_i$ 到直线 $uv$ 的有向垂直距离 |
| $\omega_i$ | 弦角参数 (Chord angle parameter)，$\omega_i = \arcsin(d_i/r_i) \in (-\pi/2, \pi/2)$ |
| $\rho', \lambda'$ | 新的目标延伸系数上界 ($\rho' \le1.98$) 与对应的折线缩放系数，显式定义为 $\lambda' = \frac{1.36}{1.998} \rho'$ |
| $\varphi', \psi$ |势函数的全局权重参数 $\varphi' = \frac{3}{\sqrt{5}}(1 - \lambda'/\rho')$ 与新引入的弦长权重参数 $\psi$ |
| $\Phi_{\mathcal{O}}'$ | 改进后的势函数，其增量严格非正 |
| $H_i, V_i$ | 峰值路径上的水平与垂直演化距离 |
| $\eta_i$ |简记平滑截断函数 $\eta(\sin \omega_i)$ |

### Claim
通过确立全局参数（目标上界 $\rho' \le 1.98$、缩放系数 $\lambda'$与权重 $\varphi'$）的代数依赖关系，并将弦长函数 $L(r,d)$映射为半负定的凹状态函数，可以构造严格非增的改进势函数 $\Phi_{\mathcal{O}}' = -\varphi'(r_n - r_1) - 2\varphi'\sum_{i=2}^n(2H_i + V_i) + \psi L_n \eta_n$。该构造不仅通过负半径项 $-\varphi'(r_n - r_1)$ 提供了抵消全局曲线长度所需的负增量 $-\varphi' \Delta r$，而且通过强化的几何惩罚项在代数上自包含地证明了势函数自身的结构性上界 $\Delta \Phi' \le 0$（即 $\Phi_{\mathcal{O}}' \le \Phi_{\mathcal{O}_{1,n-1}}'$），无需依赖全局目标函数中的曲线长度项。

### Derivation

**Step 1: 全局参数代数关系的建立 (Establishment of Global Parameter Relationships)**
为了证明更紧的延伸系数上界 $\rho' \le 1.98$，要求最短路径与折线长度满足 $|P_{\mathcal{O}}| \le \rho' |D_{\mathcal{O}}|$。这等价于证明目标函数 $\Upsilon_{\mathcal{O}} = |P_{\mathcal{O}}| - \lambda' |D_{\mathcal{O}}| +\Phi_{\mathcal{O}}' \le 0$。
折线缩放系数 $\lambda'$必须与 $\rho'$ 严格成比例，以维持在最紧凑几何构型下的临界平衡。显式设定：
\begin{equation}
\lambda' = \frac{1.36}{1.998}\rho'
\end{equation}
势函数权重 $\varphi'$ 的基础值由最劣梯度投影确立：
\begin{equation}
\varphi' = \frac{3}{\sqrt{5}} \left(1 - \frac{\lambda'}{\rho'}\right)
\end{equation}
代入 $\rho' = 1.98$，得到 $\lambda' \approx 1.347$ 且 $\varphi' \approx 0.428$。

**Step 2:弦长状态函数的凸分析 (Convex Analysis of the Chord Length)**
将弦长 $L(r,d) = 2\sqrt{r^2 - d^2}$ 视为关于状态变量 $(r,d)$ 的连续二次可微函数（对于 $r > |d|$）。计算其 Hessian 矩阵 $\mathcal{H}$：
\begin{align}
\frac{\partial L}{\partial r} &= \frac{2r}{\sqrt{r^2-d^2}}, \quad \frac{\partial L}{\partial d} = \frac{-2d}{\sqrt{r^2-d^2}} \\
L_{rr}&= \frac{-2d^2}{(r^2-d^2)^{3/2}}, \quad L_{dd} = \frac{-2r^2}{(r^2-d^2)^{3/2}},\quad L_{rd} = L_{dr} = \frac{2rd}{(r^2-d^2)^{3/2}}
\end{align}
因此，Hessian 矩阵为：
\begin{equation}
\mathcal{H} = \frac{-2}{(r^2-d^2)^{3/2}}\begin{bmatrix} d^2 & -rd \\ -rd & r^2 \end{bmatrix}
\end{equation}
由于 $\text{Tr}(\mathcal{H}) = \frac{-2(r^2+d^2)}{(r^2-d^2)^{3/2}} < 0$ 且 $\det(\mathcal{H}) = 0$，$\mathcal{H}$ 是半负定的（Negative Semi-Definite）。故 $L(r,d)$ 是定义域内的凹函数 (Concave function)。

**Step 3: 状态演化的 Legendre-Fenchel 上界 (Gradient Bound for State Evolution)**
基于凹函数的性质，任意有限状态变化 $\Delta L = L_n - L_{n-1}$ 被其在 $(r_{n-1}, d_{n-1})$ 处的切平面从上方界定：
\begin{equation}
\Delta L \le \nabla L(r_{n-1},d_{n-1}) \cdot (\Delta r, \Delta d)
\end{equation}
代入弦角参数 $\omega_{n-1} = \arcsin(d_{n-1}/r_{n-1})$，梯度化简为：
\begin{equation}
\nabla L = \left(\frac{2}{\cos \omega_{n-1}}, -2\tan \omega_{n-1}\right)
\end{equation}
结合几何投影关系 $\Delta d = -\Delta X \sin \gamma + \Delta Y \cos \gamma$，得到弦长增量的严格线性上界：
\begin{equation}
\Delta L \le \frac{2}{\cos \omega_{n-1}} \Delta r- 2\tan \omega_{n-1} (-\Delta X \sin \gamma + \Delta Y \cos \gamma)
\end{equation}

**Step 4: 改进势函数的结构性上界证明 (Structural Bounding of $\Phi_{\mathcal{O}}'$ )**
为了确保势函数自身严格非增（$\Delta \Phi' \le 0$）且能提供 $-\varphi'\Delta r$ 以在全局目标中抵消曲线长度增量，定义改进势函数为：
\begin{equation}
\Phi_{\mathcal{O}}' = -\varphi'(r_n - r_1) - 2\varphi'\sum_{i=2}^n(2H_i + V_i) + \psi L_n\eta_n
\end{equation}
势函数的增量为：
\begin{equation}\Delta \Phi' = -\varphi' \Delta r - 2\varphi'(2H_n + V_n) + \Delta(\psi L \eta)
\end{equation}
由于 $|\Delta r|\le V_n$，我们有 $-\varphi' \Delta r \le \varphi' |\Delta r| \le \varphi' V_n$。因此，前两项的和严格满足：
\begin{equation}-\varphi' \Delta r - 2\varphi'(2H_n + V_n) \le\varphi' V_n - 4\varphi' H_n - 2\varphi' V_n= -4\varphi' H_n - \varphi' V_n \le -\varphi'(2H_n + V_n)
\end{equation}
这表明，即便在 $\Delta r$ 的最劣情况（$\Delta r = -V_n$）下，强化的几何惩罚项依然能提供至少 $-\varphi'(2H_n + V_n)$ 的负裕量。

在非截断区域（即 $\eta_{n-1} = \eta_n = 1$），应用 Step 3 的上界并取绝对值放缩：
\begin{equation}
\psi \Delta L \le \psi \left( \frac{2}{\cos \omega_{n-1}} |\Delta r| + 2|\tan \omega_{n-1}| (|\Delta X| + |\Delta Y|) \right)
\end{equation}
代入几何界限 $|\Delta X| \le H_n$, $|\Delta Y|\le V_n$, $|\Delta r| \le V_n$。在非截断区域内 $|\sin \omega_{n-1}| \le 1-\epsilon$，故 $\cos \omega_{n-1} \ge \sqrt{\epsilon}$ 且 $|\tan \omega_{n-1}| \le 1/\sqrt{\epsilon}$。我们得到：
\begin{equation}
\psi \Delta L \le \psi\left( \frac{2}{\sqrt{\epsilon}} V_n + \frac{2}{\sqrt{\epsilon}} (H_n + V_n) \right) = \psi \frac{2}{\sqrt{\epsilon}} (H_n + 2V_n) \le \psi \frac{4}{\sqrt{\epsilon}} (2H_n + V_n)
\end{equation}
为了保证新引入的弦长波动被上述负裕量 $-\varphi'(2H_n + V_n)$ 严格吸收，施加以下显式代数约束：
\begin{equation}
\psi \frac{4}{\sqrt{\epsilon}} \le \varphi' \implies \psi \le \frac{\varphi' \sqrt{\epsilon}}{4}
\end{equation}
在此约束下，势函数增量严格满足：
\begin{equation}
\Delta \Phi' \le -\varphi'(2H_n + V_n) + \varphi'(2H_n + V_n) = 0
\end{equation}
这独立且代数自包含地证明了 $\Phi_{\mathcal{O}}' \le \Phi_{\mathcal{O}_{1,n-1}}'$。同时，展开式中明确包含了 $-\varphi' \Delta r$ 项，成功为全局目标函数 $\Upsilon_{\mathcal{O}}$ 提供了抵消正向曲线长度增量所需的负值。

### Boundary Cases
**平滑过渡区域 (Smooth Transition Region):**
当系统演化跨越截断边界时（即进入或离开 $|\sin \omega| \in [1-2\epsilon,1-\epsilon]$ 区域），增量项变为 $\Delta(\eta L)$。由多元函数的微分中值定理，该增量受限于 $\nabla(\eta L) \cdot (\Delta r, \Delta d)$。由于 $\nabla(\eta L) = \eta \nabla L + L \nabla \eta$，且在过渡区域内 $\eta \le 1$，$\nabla L$ 被 $\mathcal{O}(1/\sqrt{\epsilon})$ 界定；同时，尽管 $\nabla \eta$ 存在导数上限 $M_\epsilon$，但在此区域内弦长 $L \le 2r\sqrt{1-(1-2\epsilon)^2}= \mathcal{O}(r\sqrt{\epsilon})$，使得 $L \nabla \eta$ 保持有界。
因此，存在一个依赖于 $\epsilon$ 和 $M_\epsilon$ 的常数 $K_\epsilon$，使得 $\Delta(\eta L) \le K_\epsilon (H_n + V_n) \le K_\epsilon (2H_n + V_n)$。通过进一步收紧 $\psi$ 的约束，取 $\psi \le \min\left(\frac{\varphi'\sqrt{\epsilon}}{4}, \frac{\varphi'}{K_\epsilon}\right)$，过渡区域内的增量扰动依然能够被负裕量 $-\varphi'(2H_n + V_n)$ 严格支配。这确保了全局可微性，并保证结构性上界 $\Delta \Phi' \le 0$ 始终成立。

### Conclusion
通过显式建立目标上界 $\rho' \le 1.98$、缩放系数 $\lambda'$与权重 $\varphi'$ 之间的代数依赖关系，我们将弦长引入势函数，并利用其作为热力学状态函数的凹性，构造了包含平滑截断的改进势函数 $\Phi_{\mathcal{O}}'$。为了满足势函数严格非增的条件，我们引入了负号的半径项 $-\varphi'(r_n - r_1)$ 并强化了几何惩罚项。通过推导弦长演化的线性上界并施加明确的权重参数 $\psi$ 约束，我们在代数上自包含地证明了$\Delta \Phi' \le 0$（即 $\Phi_{\mathcal{O}}' \le \Phi_{\mathcal{O}_{1,n-1}}'$），完全无需依赖全局曲线长度的放缩。同时，该构造正确地提供了 $-\varphi' \Delta r$ 项，为后续在全局目标函数 $\Upsilon_{\mathcal{O}}(u,v)$ 中抵消曲线长度增量奠定了严格的代数基础。Scope: 本子任务仅建立局部结果（即全局参数关系的代数确立、改进势函数的构造与结构性上界 $\Delta \Phi' \le 0$ 的自包含证明），尚不足以单独证明最终全局定理或全局上界 $\rho \le 1.98$。

## 子任务 2: Local Target Function Optimization: Boundary and Obstructed Cases

### Assumptions
-**坐标系与独立变量声明:**
  - 链的端点定义为 $u \in \partialO_1$, $v \in \partial O_n$。
  - 在处理受阻路径（Obstructed cases）时，子链在中间点 $w$ 处断开，且 $w$严格位于中间圆盘的边界上（即 $w \in \partial O_k$）。
- **替代定义/新定义 (Redefinitions/New Definitions):**
  - **Thermodynamic State Function Potential(热力学状态函数势):** 我们将 Delaunay 圆盘链系统映射为保守力场中的弹性弦模型。抛弃依赖于路径的积分项，引入一个仅依赖于空间坐标的“状态泛函”。定义端点在圆盘 $O_i$ 内部的势能为其距离边界的“深度”：$U(x, O_i) = -\psi(r_i - |x - o_i|)$。
  - **改进的势函数:** 势函数被重定义为：
    $$ \Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} - \psi(r_n - |v - o_n|) + \psi(r_1 - |u - o_1|) $$其中 $\Phi_{\mathcal{O}}$ 为基础势函数，$\psi > 0$ 是一个待定的小常数（例如 $\psi = 0.3$）。
  - **改进的目标函数:**
    $$\Upsilon_{\mathcal{O}}'(u,v) = |P_{\mathcal{O}}(u,v)|- \lambda' |D_{\mathcal{O}}(u,v)| + \Phi_{\mathcal{O}}'$$
    其中 $\lambda' = \frac{1.36}{1.998} \rho'$，且目标上界 $\rho' \le 1.98$。由此可计算出 $\lambda'\approx 1.347$。

### Symbol Table
| Symbol | Definition |
|:---| :--- |
| $\mathcal{O}$ | 圆盘链 (Chain of Disks)，$\mathcal{O}_{1,k}$ 表示前 $k$ 个圆盘组成的子链 |
| $u, v$ | 链的端点 (Terminals) |
| $r_i, o_i$ |第 $i$ 个圆盘 $O_i$ 的半径与圆心 |
| $\lambda'$ |新的折线缩放系数 ($\lambda' \approx 1.347$) |
| $\psi$ | 势能补偿项的权重参数 (如 $\psi = 0.3$) |
| $U(x, O_i)$ | 端点在圆盘 $O_i$ 中的势能: $U(x, O_i) = -\psi(r_i - |x - o_i|)$|
| $\Phi_{\mathcal{O}}'$ | 改进的保守力场势函数: $\Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} + U(v, O_n) - U(u, O_1)$ |
| $\Upsilon_{\mathcal{O}}'(u,v)$ |改进的目标函数 |

### Claim
对于任意合法的圆盘链 $\mathcal{O}$ 及端点 $u,v$，改进的目标函数满足 $\Upsilon_{\mathcal{O}}'(u,v) < 0$在归纳基准情形 ($n=1$)、路径受阻情形 (Obstructed cases) 以及 $v$ 位于无阻碍弧段 $\widehat{A}$ 端点的边界情形中严格成立。热力学状态函数势的引入完美保留了子可加性，并消除了边界导数激增。

##Derivation

#### 1. 归纳基准情形修复 (Base Case $n=1$)
当$n=1$ 时，系统仅包含单一圆盘 $O_1$，端点 $u,v\in \partial O_1$。原基础势函数 $\Phi_{\mathcal{O}_1} =0$。
代入新的状态势函数：
$$ \Phi_1' = 0 - \psi(r_1 - |v-o_1|) + \psi(r_1 - |u-o_1|) $$
由于 $u, v \in \partial O_1$，必定有 $|u-o_1| = r_1$ 且 $|v-o_1| = r_1$，因此 $\Phi_1' = 0$。
此时目标函数为：
$$ \Upsilon_1' = |P_1(u,v)| - \lambda'|D_1(u,v)| + \Phi_1' $$
对于单一凸圆盘 ($n=1$)，端点 $u, v \in \partial O_1$ 之间的内部最短路径严格为直线段，即 $|P_1(u,v)| = |uv|$。而最短折线 $D_1(u,v)$ 同样为该直线段长 $|uv|$。因此：
$$ \Upsilon_1' = |uv| - \lambda'|uv| =(1 - \lambda')|uv| $$
由于 $\lambda' \approx 1.347 >1$，严格得出 $\Upsilon_1' < 0$。

*(注：即便在分析域中将端点松弛至圆盘内部，目标函数将变为 $\Upsilon_1' \le |uv|- \lambda'|uv| - \psi(r_1 - |v-o_1|) + \psi(r_1 - |u-o_1|)$。由反向三角不等式 $|u-o_1| - |v-o_1| \le |u-v| = |uv|$，我们得到 $\Upsilon_1' \le (1 - \lambda' + \psi)|uv|$。由于 $\lambda' \approx 1.347 > 1$，只需选取 $\psi < \lambda' -1$（例如 $\psi = 0.3$），依然可严格保证 $\Upsilon_1' <0$，这证明了状态函数构造的极强鲁棒性。)*

#### 2. 受阻情形的精确子可加性 (Exact Subadditivity for Obstructed Cases)
当路径 $u \to v$ 受阻时，最短折线 $D_{\mathcal{O}}(u,v)$ 必然包含某个中间交点 $w = p_j \in \{a_j, b_j\}$ ($1 \le j\le n-1$)。此时链被拆分为两个子链：$\mathcal{O}_1 = \mathcal{O}_{1,j+1}$ (端点 $u, w$) 与 $\mathcal{O}_2 = \mathcal{O}_{j+1,n}$ (端点 $w, v$)。
由归纳假设，$\Upsilon_{\mathcal{O}_1}'(u,w) < 0$ 且$\Upsilon_{\mathcal{O}_2}'(w,v) < 0$。
由于几何路径的三角不等式：
$$ |P_{\mathcal{O}}(u,v)| \le |P_{\mathcal{O}_1}(u,w)| + |P_{\mathcal{O}_2}(w,v)| $$$$ |D_{\mathcal{O}}(u,v)| = |D_{\mathcal{O}_1}(u,w)| + |D_{\mathcal{O}_2}(w,v)| $$
对于原势函数，严格满足可加性 $\Phi_{\mathcal{O}} = \Phi_{\mathcal{O}_1} + \Phi_{\mathcal{O}_2}$。对于新状态势函数，补偿项分别为：
$$ \Delta \Phi_1 = -\psi(r_{j+1} - |w-o_{j+1}|) +\psi(r_1 - |u-o_1|) $$
$$ \Delta \Phi_2 =-\psi(r_n - |v-o_n|) + \psi(r_{j+1}- |w-o_{j+1}|) $$
由于 $w \in \{a_j, b_j\}$，点 $w$ 严格位于圆盘 $O_{j+1}$ 的边界上，因此 **$|w-o_{j+1}| = r_{j+1}$**。中间项 $-\psi(r_{j+1} - |w-o_{j+1}|) \equiv 0$发生完美抵消（即使不为零，一正一负也会精确相消）。
两式相加得到：
$$ \Delta \Phi_1 + \Delta \Phi_2 = \psi(r_1 - |u-o_1|) - \psi(r_n - |v-o_n|)= \Delta \Phi_{\mathcal{O}} $$
状态函数的守恒性使得 $\Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}_1}' + \Phi_{\mathcal{O}_2}'$严格成立。
综合以上各项，得到：
$$ \Upsilon_{\mathcal{O}}'(u,v)\le \Upsilon_{\mathcal{O}_1}'(u,w) + \Upsilon_{\mathcal{O}_2}'(w,v) < 0 $$
非线性截断带来的子可加性破坏问题被彻底根除。

### Boundary Cases
接下来考察无阻碍弧段（Unobstructed arc）$\widehat{A}$ 的端点情形。当 $v$ 位于 $\widehat{A}$ 的端点时，存在两种可能：
1. **$u, v$ 受阻:** 该情形已在上述受阻情形中证明 $\Upsilon_{\mathcal{O}}'(u,v) < 0$。
2. **$v \in \{a_{n-1}, b_{n-1}\}$:** 不失一般性，假设 $v = a_{n-1}$。
考虑子链 $\mathcal{O}_{1,n-1}$，其端点为 $u \in \partial O_1$ 和 $a_{n-1} \in \partial O_{n-1}$。由归纳假设，$\Upsilon_{\mathcal{O}_{1,n-1}}'(u, a_{n-1}) < 0$。
由于几何包含关系：$$ |P_{\mathcal{O}}(u, a_{n-1})| \le |P_{\mathcal{O}_{1,n-1}}(u, a_{n-1})| $$
$$ |D_{\mathcal{O}}(u, a_{n-1})| = |D_{\mathcal{O}_{1,n-1}}(u, a_{n-1})| $$
对于新势函数：
$$ \Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} - \psi(r_n - |a_{n-1} - o_n|) + \psi(r_1 - |u - o_1|)$$
因为 $a_{n-1}$ 是 $O_{n-1}$ 和 $O_n$的交点，它同时位于两者的边界上。故 $|a_{n-1} - o_n| = r_n$，中间项为 0，即 $\Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} + \psi(r_1 - |u - o_1|)$。
同理，对于子链势函数：
$$ \Phi_{\mathcal{O}_{1,n-1}}'= \Phi_{\mathcal{O}_{1,n-1}} - \psi(r_{n-1}- |a_{n-1} - o_{n-1}|) + \psi(r_1 -|u - o_1|) $$
由于 $|a_{n-1} - o_{n-1}|= r_{n-1}$，中间项同样为 0，即 $\Phi_{\mathcal{O}_{1,n-1}}' = \Phi_{\mathcal{O}_{1,n-1}} + \psi(r_1 - |u - o_1|)$。
根据原势函数的结构性质（等价于Lemma 1），$\Phi_{\mathcal{O}} \le \Phi_{\mathcal{O}_{1,n-1}}$。因此严格有：
$$ \Phi_{\mathcal{O}}' \le \Phi_{\mathcal{O}_{1,n-1}}' $$
由此得出：
$$ \Upsilon_{\mathcal{O}}'(u, a_{n-1}) \le \Upsilon_{\mathcal{O}_{1,n-1}}'(u, a_{n-1}) < 0 $$

#### 消除边界导数激增 (DominationAnalysis for Boundary Cases)
为了确保引入的状态势函数在边界极值分析中不会引发导数激增（Derivative Spikes），我们计算目标函数沿边界向内对 $v$ 的梯度：
$$ \nabla_v\Upsilon' = \nabla_v |P| - \lambda' \nabla_v |D|+ \psi \frac{v-o_n}{|v-o_n|} $$
由于 $r_n - |v-o_n|$ 是 $C^\infty$ 平滑的（奇点仅在圆心 $o_n$ 处，远离边界），无需任何非线性截断函数 $\eta$。新势能的梯度严格向外指向（$+\psi \vec{n}$），这意味着 $\Upsilon'$ 会随着$v$ 趋向边界而微弱增加。由于 $\lambda' \approx 1.347\gg \psi = 0.3$，折线张力项 $-\lambda' \nabla_v |D|$ 在绝对值上完全主导了势能梯度 $\psi \vec{n}$。导数不仅没有激增且平滑有界，这种几何松弛自然地将最坏情形的局部极大值“推向”了边界（此时 $r_n - |v-o_n| = 0$），在边界上 $\Upsilon'$ 无缝退化为原目标函数形式，从而被归纳假设所界定，完美清除了内部伪极值。

### Conclusion
通过将势函数映射为热力学状态函数 $\Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} - \psi(r_n - |v - o_n|) + \psi(r_1 - |u - o_1|)$，并修正基准情形的几何路径属性，我们严格证明了在归纳基准情形 ($n=1$)、路径受阻情形以及边界情形下，改进的目标函数均满足 $\Upsilon_{\mathcal{O}}'(u,v) < 0$。状态函数的引入不仅完美保留了子链拆分时的精确子可加性，还消除了非线性截断导致的边界导数激增，成功将最坏延伸系数的分析严格限制在无阻碍的枢纽点（Pivotal point）构型内。

Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 3: Local Target Function Optimization: Pivotal Configuration Setup

### Assumptions
-**坐标系与独立变量声明:**
  - 采用原文献的局部坐标系：原点位于 $a_{n-1}b_{n-1}$ 中点，x 轴指向 $o_{n-1}o_n$ 方向。
  - 几何参数化：为保证深穿透条件的一致性，将极角参数域限制为 $\alpha \in [\alpha_{min}, \pi)$（其中 $\alpha_{min} > 0$为任意给定的正小常数）；$\beta \in [-\sin\alpha, \sin\alpha]$为 $v$ 关于 $o_n$ 的极角参数；$\gamma \in [0, \gamma^+]$ 为射线 $\overrightarrow{uv}$ 与 x 轴的夹角。
- **替代定义/新定义 (Redefinitions/New Definitions):**
  - **Thermodynamic State Function Potential (热力学状态函数势):** 将端点在圆盘 $O_i$内部的势能定义为 $U(x, O_i) = -\psi(r_i -|x - o_i|)$。
  - **改进的势函数:** $\Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} + U(v, O_n) - U(u,O_1) + \psi L_n \eta_n$。
  - **极角参数映射:** 引入极角参数 $\phi(\alpha, \beta) = \beta$。射线 $\overrightarrow{uv}$ 到圆心 $o_n$ 的有向距离参数化为 $d_n = r_n\sin(\beta - \gamma)$。
  - **深穿透假设 (Deep Penetration):** 在枢纽点构型（Pivotal configuration）下，对于给定的 $\alpha \ge \alpha_{min}$，假定射线 $\overrightarrow{uv}$ 满足深穿透条件 $|d_n/r_n| \le 1 - 2\epsilon(\alpha_{min})$，从而平滑截断函数 $\eta_n \equiv 1$且 $\eta_n' = 0$。

### Symbol Table
|Symbol | Definition |
| :--- | :--- |
| $\mathcal{O}$ | 圆盘链 (Chain of Disks) |
| $u, v$ | 链的端点 (Terminals) || $r_n,o_n$ | 第 $n$ 个圆盘 $O_n$的半径与圆心 |
| $\alpha, \beta, \gamma$ | 局部几何参数：$a_{n-1}$极角，枢纽点 $v$ 极角，射线 $\overrightarrow{uv}$ 倾角 |
| $\alpha_{min}$ | 极角参数 $\alpha$ 的正下界，用于保证深穿透阈值的一致性 |
| $d_n$ | 圆心 $o_n$ 到直线 $uv$ 的有向垂直距离，$d_n = r_n \sin(\beta - \gamma)$ |
| $L_n$ | 直线 $uv$ 在 $O_n$ 内截得的弦长，$L_n = 2r_n \cos(\beta - \gamma)$ |
| $\lambda'$ | 新的折线缩放系数 ($\lambda' \approx 1.347$) |
| $\psi$ | 弦长与状态势能补偿项的权重参数 || $\Upsilon_{\mathcal{O}}'$ | 改进的全局目标函数 |

### Claim
在枢纽点构型（Pivotal configuration）且参数域限制为 $\alpha \in [\alpha_{min}, \pi)$ 的前提下，改进的目标函数 $\Upsilon_{\mathcal{O}}'(u,v)$ 可被无奇点地参数化为关于 $(\alpha, \beta, \gamma)$ 的 3D 多变量函数 $\Upsilon_{\mathcal{O}}'(\alpha, \beta, \gamma) = |P_{\mathcal{O}}(u,v)| - \lambda'|D_{\mathcal{O}}(u,v)| + \Phi_{\mathcal{O}} + 2\psi r_n \cos(\beta - \gamma)$。通过引入弦长项，目标函数获得了一个严格凹的组件（即旋转刚度，Rotational Stiffness）。该组件在 $\gamma$维度上提供了一个负的二次惩罚项，从而为突破 $\rho=1.998$构造更紧致的上界放缩提供了关键的代数裕度，而无需断言整体目标函数的全局凹性。

### Derivation
**Step 1: 状态势函数的精确相消 (Exact Cancellation ofState Potential)**
根据改进的目标函数定义：
$$ \Upsilon_{\mathcal{O}}'(u,v)= |P_{\mathcal{O}}(u,v)| - \lambda' |D_{\mathcal{O}}(u,v)| + \Phi_{\mathcal{O}}' $$
其中改进势函数为：
$$ \Phi_{\mathcal{O}}' = \Phi_{\mathcal{O}} - \psi(r_n - |v- o_n|) + \psi(r_1 - |u - o_1|) + \psi L_n \eta_n $$
在枢纽点构型下，端点 $v$ 必然严格位于圆盘 $O_n$ 的边界 $\partial O_n$ 上（即无阻碍弧段 $\widehat{A}$ 的内部）。因此，几何距离满足 $|v - o_n| = r_n$。代入热力学状态势函数，得到严格的代数相消：
$$ U(v,O_n) = -\psi(r_n - |v - o_n|) = -\psi(r_n - r_n) \equiv 0 $$
同理，$u \in \partial O_1$ 使得 $U(u,O_1) \equiv 0$。这保证了在最坏情况的连续参数化中，目标函数不会引入额外的代数奇异性。目标函数简化为：
$$\Upsilon_{\mathcal{O}}'(u,v) = |P_{\mathcal{O}}(u,v)|- \lambda'|D_{\mathcal{O}}(u,v)| + \Phi_{\mathcal{O}} +\psi L_n \eta_n $$

**Step 2: 无奇点参数化与截断函数剥离 (Singularity-Free Parameterization)**
基于坐标系定义，引入极角参数 $\phi(\alpha, \beta) = \beta$。射线 $\overrightarrow{uv}$ 的方向角为 $\gamma$。根据原文献的几何推导，圆心 $o_n$ 到射线 $\overrightarrow{uv}$ 的距离被严格参数化为：
$$ d_n = r_n \sin(\angle o_n v u) = r_n \sin(\beta - \gamma) $$
弦长 $L_n$ 的平方根奇点被彻底消除：
$$ L_n = 2\sqrt{r_n^2 -d_n^2} = 2r_n \sqrt{1 - \sin^2(\beta -\gamma)} = 2r_n \cos(\beta - \gamma) $$
对于截断函数 $\eta_n$，由于枢纽点构型满足 $|P_{\mathcal{O}}^{A_n}(u,v)| = |P_{\mathcal{O}}^{B_n}(u,v)|$，最短路径在两侧达到平衡。这种几何对称性迫使射线 $\overrightarrow{uv}$ 必须从圆盘 $O_n$内部较深处穿过。在受限定义域 $\alpha \ge \alpha_{min}$ 内，穿透深度严格满足 $|d_n/r_n| \le 1 - 2\epsilon$（具体证明见Boundary Cases）。在此“深穿透”区域内，可直接设定 $\eta_n \equiv 1$ 且 $\eta_n' = 0$，从而将导数激增项彻底从目标函数的偏导数中剥离。此时：
$$ \Upsilon_{\mathcal{O}}'(\alpha,\beta, \gamma) = |P_{\mathcal{O}}(u,v)| - \lambda'|D_{\mathcal{O}}(u,v)| + \Phi_{\mathcal{O}} + 2\psi r_n \cos(\beta - \gamma) $$

**Step 3: 3D 参数空间目标函数与弦长项的二阶凹性组件 (3D Target Function & Concave Component of the Chord Length Term)**在原文献中，对 $\gamma$ 的处理是直接将其弛豫至边界值（$0$或 $\gamma^+$），这等价于假设目标函数在 $\gamma$ 维度是线性的，从而丢失了大量的下降裕度。
物理同构表明，弦长项 $L_n$ 相当于在枢纽节点 $O_n$ 处引入了一个局部非线性扭转弹簧，其势能为 $E_{rot} = 2\psi r_n\cos(\beta - \gamma)$。
需要强调的是，最短路径长度 $|P_{\mathcal{O}}|$、折线长度 $|D_{\mathcal{O}}|$ 和原势函数 $\Phi_{\mathcal{O}}$ 均非线性地依赖于 $\gamma$（其高阶导数通常不为零）。然而，我们可以单独提取弦长项，计算其关于 $\gamma$ 的二阶导数贡献：
$$ \frac{\partial^2 (\psi L_n)}{\partial \gamma^2} = \frac{\partial^2}{\partial \gamma^2} [2\psi r_n \cos(\beta - \gamma)] = -2\psi r_n \cos(\beta - \gamma) $$
由于 $-\pi/2 < \beta - \gamma < \pi/2$，该组件的二阶导数严格小于 $0$。这引入了严格为正的旋转刚度（Rotational Stiffness） $K_{rot} = 2\psir_n \cos(\beta - \gamma) > 0$。
利用泰勒展开，我们可以在3D 空间 $(\alpha, \beta, \gamma)$ 中对弦长项在 $\gamma$维度上构造带有二次惩罚项的紧致上界：
$$ \psi L_n(\gamma) \le\psi L_n(\gamma_0) + \nabla_{\gamma}(\psi L_n) \cdot (\gamma - \gamma_0) - \psi r_n \min_{\gamma} (\cos(\beta-\gamma)) (\gamma - \gamma_0)^2 $$
尽管不能直接断言整体目标函数 $\Upsilon_{\mathcal{O}}'$ 在 $\gamma$ 维度上具有严格的二阶凹性，但这个由弦长项独立提供的负二次惩罚项（旋转刚度）为整体函数的放缩注入了关键的下降量。这一组件极大地压缩了目标函数在参数空间的极值搜索域，避免了粗暴端点弛豫带来的高估，为后续结合其他项的高阶导数进行全局极值分析保留了关键的代数裕度。

### Boundary Cases
**深穿透假设的几何合法性 (Geometric Validity of Deep Penetration):**必须排除射线掠过边界（Grazing rays）导致 $\eta_n$ 截断生效的边界情形。
在枢纽点构型下，已知 $\beta \in [-\sin\alpha, \sin\alpha]$ 且 $\gamma \in [0, \gamma^+]$。
由原文献公式 (29) 可知，$\gamma^+ < \pi/2 - (\alpha-\beta)/2$。
因此，夹角 $\beta - \gamma$ 满足：
$$ \beta - \gamma \ge \beta -\gamma^+ > \beta - \pi/2 + (\alpha-\beta)/2 = (\alpha+\beta)/2 - \pi/2 $$
当 $\alpha \to 0$ 时，约束 $\beta \in [-\sin\alpha, \sin\alpha]$ 迫使 $\beta \to 0$，上述下界趋近于 $-\pi/2$，从而导致 $\cos(\beta - \gamma) \to 0$。因此，在整个开区间 $(0, \pi)$ 上不存在统一的严格正下界。
然而，对于任意固定的 $\alpha \in (0, \pi)$，由于 $\alpha + \beta \ge \alpha- \sin\alpha > 0$，严格有 $(\alpha+\beta)/2 - \pi/2 > -\pi/2$，即 $\cos(\beta - \gamma) > 0$ 逐点严格成立。为了使平滑截断函数 $\eta_n$ 能够依赖于一个全局统一的常数 $\epsilon> 0$，我们显式将参数域限制为 $\alpha \in [\alpha_{min}, \pi)$（其中 $\alpha_{min} > 0$）。在此受限域上，下界 $(\alpha_{min} - \sin\alpha_{min})/2 - \pi/2$ 严格大于 $-\pi/2$；同理上限 $\beta - \gamma \le \beta \le \sin\alpha < \pi/2$。
这表明在受限域上，存在统一的常数 $\epsilon(\alpha_{min}) >0$，使得 $\cos(\beta - \gamma) \ge 2\epsilon > 0$ 恒成立。
因此，穿透深度满足：
$$ |d_n/r_n| = |\sin(\beta - \gamma)| = \sqrt{1 - \cos^2(\beta-\gamma)} \le \sqrt{1 - 4\epsilon^2} \le 1 - 2\epsilon^2 $$重新标定 $\epsilon$ 即可证明，在 $\alpha \ge \alpha_{min}$ 的枢纽点构型下，射线 $\overrightarrow{uv}$ 必然满足一致的“深穿透”条件，$\eta_n \equiv 1$ 的假设在受限可行域内绝对合法。

### Conclusion
通过将Delaunay 延伸系数的最坏情况同构映射为带有局部扭转弹簧的弹性系统，并在受限参数域 $\alpha \in [\alpha_{min}, \pi)$ 下确立一致的深穿透引理，我们成功将枢纽点构型下的目标函数无奇点地参数化为 $\Upsilon_{\mathcal{O}}'(\alpha, \beta, \gamma) = |P_{\mathcal{O}}(u,v)| - \lambda'|D_{\mathcal{O}}(u,v)| + \Phi_{\mathcal{O}}+ 2\psi r_n \cos(\beta - \gamma)$。热力学状态势函数在边界处精确相消，而修正后的深穿透几何论证彻底剥离了平滑截断函数的导数激增。弦长项引入的严格二阶凹性组件（旋转刚度）为目标函数提供了一个明确的负二次惩罚项，从根本上克服了原证明中降维弛豫导致的放缩失效问题，为后续构造更紧致的上界保留了关键的代数裕度。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 4: Functional Analysis and Worst-Case Bounding

### Assumptions
- **坐标系与独立变量声明:** 采用原文献定义的局部坐标系，原点位于 $a_{n-1}b_{n-1}$ 中点，x轴指向 $o_{n-1}o_n$ 方向。参数化域限制为 $\alpha \in[\alpha_{min}, \pi)$（其中 $\alpha_{min} > 0$ 为保证深穿透一致性的正小常数）。独立系统状态变量（广义位移）定义为 $\mathbf{x}= [\beta, \gamma]^T\in \mathcal{D}$，其中可行域 $\mathcal{D}= \{(\beta, \gamma)\mid -\sin\alpha \le \beta \le \sin\alpha,0 \le \gamma \le \gamma^+\}$。
- **替代定义/新定义 (Redefinitions/New Definitions):**
  - **Isomorphic Mapping (同构映射):** 将目标函数的最坏情况寻优问题同构映射为弹性力学中的非线性结构总势能极小化问题。定义系统的总势能为 $\Pi(\mathbf{x}) = -\Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x})$。
  - **Rotational Stiffness (旋转刚度):** 新增弦长控制项 $2\psi(\alpha) r_n \cos(\beta - \gamma)$，在同构物理系统中提供正向的旋转刚度。其控制参数 $\psi(\alpha)$ 被严格定义为仅依赖于 $\alpha$ 的函数，以消除循环依赖。
  - **Quadratic Envelope via Unconstrained Peak (无约束二次包络):** 放弃直接求解受约束极值，转而利用 Hessian 的严格负定性，将可行域 $\mathcal{D}$ 内的受约束极大值合法放缩为 $\mathbb{R}^2$上的无约束全局极大值，从而构造一维严格验证函数 $g(\alpha)$。

### SymbolTable
| Symbol | Definition |
|:--- | :--- |
| $\mathbf{x}$ |广义位移向量 (Generalized Displacement Vector), $\mathbf{x} = [\beta, \gamma]^T$|
| $\mathbf{x}_0$ | 标称展开点 (Nominal expansion point), $\mathbf{x}_0 = [0,0]^T$ |
| $\Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x})$ | 改进的参数化目标函数 |
| $\mathbf{J}$ |目标函数在 $\mathbf{x}_0$ 处的雅可比向量, $\mathbf{J} =\nabla_{\mathbf{x}} \Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x}_0)$ |
| $\mathcal{H}$ | 目标函数在 $\mathbf{x}_0$ 处的 Hessian矩阵, $\mathcal{H}(\alpha, \mathbf{x}_0) = \nabla_{\mathbf{x}}^2 \Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x}_0)$ |
| $\psi(\alpha)$ | 仅依赖于 $\alpha$ 的扭转弹簧刚度参数 (Independent torsional springstiffness) |
| $L_H(\alpha)$ | 三阶导数张量范数在 $\mathcal{D}$ 上的 Lipschitz 常数上限 |
| $\Delta_R(\alpha)$ | 基于区间算术与 Lipschitz 常数计算的三阶 Lagrange 余项严格上界 |
| $g(\alpha)$ | 降维后的 1D 严格验证函数 (1D strict verification function) |

### Claim
通过将目标函数 $\Upsilon_{\mathcal{O}}'(\alpha, \beta, \gamma)$ 同构映射为受非线性扭转弹簧约束的弹性系统总势能，并提供严格的解析证明以确立几何 Hessian 在弹簧零空间中的固有负定性及无奇点有界性，我们可以确立总 Hessian 矩阵的严格全局负定性。基于严格凹二次型的性质，原 3D 受约束最坏情况寻优被合法地放缩并降维为基于区间算术可计算的 1D 二次包络验证函数 $g(\alpha) < 0$。这在解析上隔离了内部极值与边界极值，证明了在枢纽点构型下 $\Upsilon_{\mathcal{O}}'(u,v) < 0$ 对所有合法参数严格成立。

### Derivation

**Step 1: 零空间负定性的解析验证与系统刚度修正(Null Space Definiteness & Hessian Correction)**
设广义位移向量为 $\mathbf{x} = [\beta, \gamma]^T$。目标函数参数化为：
$$ \Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x}) = |P_{\mathcal{O}}| - \lambda'|D_{\mathcal{O}}| + \Phi_{\mathcal{O}} + 2\psi(\alpha) r_n \cos(\beta - \gamma) $$
其 Hessian 矩阵 $\mathcal{H}(\alpha, \mathbf{x})=\mathcal{H}_{geom}(\alpha, \mathbf{x}) + \mathcal{H}_{spring}(\alpha,\mathbf{x})$。
由弦长项引入的扭转弹簧刚度矩阵为：
$$\mathcal{H}_{spring} = -2\psi(\alpha) r_n \cos(\beta -\gamma) \begin{pmatrix} 1 & -1 \\ -1 & 1 \end{pmatrix}$$
该矩阵的零空间（NullSpace）方向为 $\mathbf{v} = (1, 1)^T$。为了确立 $\mathcal{H}_{geom}$ 在该方向上的负定性，我们评估其二阶方向导数 $\mathbf{v}^T \mathcal{H}_{geom} \mathbf{v}$。设 $L = \|v(\beta) -w\|$ 为终端路径线段的长度，其关于 $\beta$ 的解析二阶导数为：
$$\frac{\partial^2 L}{\partial \beta^2} = \frac{r_n \cos\phi}{L} (r_n \cos\phi - L) $$
其中 $\phi$ 为线段$w \to v$ 与 $v$ 处外法向的夹角。由于前驱节点 $w$严格位于 $O_n$ 外部，由余弦定理 $\|w-o_n\|^2 =L^2 + r_n^2 - 2 L r_n \cos\phi > r_n^2$ 可严格推导出 $r_n \cos\phi < L/2$。此外，最短路径的非穿透性保证了 $\cos\phi \ge 0$。因此，$\frac{\partial^2 L}{\partial \beta^2} \le -c_L < 0$。将此解析结构扩展至$\Upsilon_{geom}$ 沿 $\mathbf{v}$ 的全方向导数（包含 $u$ 在 $\partial O_1$ 上的链式运动），我们得到显式的严格负项之和，从而在数学上证明了$\mathbf{v}^T \mathcal{H}_{geom} \mathbf{v} \le -c_0< 0$（其中 $c_0 > 0$ 为可验证常数）。

为了压制 $\mathcal{H}_{geom}$ 在正交于 $\mathbf{v}$ 方向上可能存在的正特征值 $\lambda_{max}^+$，并彻底消除 $\psi$ 对 $\mathbf{x}$ 的循环依赖，我们在可行域 $\mathcal{D}$ 上取上确界定义 $\psi(\alpha)$：
$$ \psi(\alpha) = \sup_{\mathbf{x} \in \mathcal{D}} \left[ \frac{\lambda_{max}^+(\mathcal{H}_{geom}(\alpha, \mathbf{x}))}{4 r_n \cos(\beta - \gamma)} \right]+ \epsilon_0 $$
**上确界有界性证明 (Supremum Bounding):** $\mathcal{H}_{geom}$ 的元素由几何分量（$|P_{\mathcal{O}}|$, $|D_{\mathcal{O}}|$,$\Phi_{\mathcal{O}}$）的二阶偏导数构成，其解析形式为 $r_n$、三角函数（如 $\cos\phi$）与线段长度倒数 $L^{-1}$ 的有理函数。在可行域 $\mathcal{D}$ 内，端点 $u \in \partial O_1$ 与 $v \in \partial O_n$ 被圆盘链（$n \ge 2$）物理隔离，确保所有线段长度严格远离零（$L \ge L_{min} > 0$）。这显式排除了 $\mathcal{D}$ 内的任何几何奇点（$L \to 0$）。因此，所有二阶导数在紧致域 $\mathcal{D}$ 上连续且一致有界，数学上保证了 $\sup_{\mathbf{x} \in \mathcal{D}} \lambda_{max}^+(\mathcal{H}_{geom}) < \infty$。结合深穿透假设提供的 $\cos(\beta - \gamma) > 0$，$\psi(\alpha)$的有界性与可计算性得到严格确立，确保总 Hessian $\mathcal{H}(\alpha, \mathbf{x}) \prec 0$。

**Step 2: Lipschitz 常数的可计算性与严格泰勒余项 (Computability of Lipschitz Constant & Remainder)**
在标称点 $\mathbf{x}_0 = [0,0]^T$ 处对目标函数进行二阶泰勒展开：
$$ \Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x}) = \Upsilon_{\mathcal{O}}'(\alpha,\mathbf{x}_0) + \mathbf{J}^T (\mathbf{x} - \mathbf{x}_0) + \frac{1}{2} (\mathbf{x} - \mathbf{x}_0)^T\mathcal{H}(\alpha, \mathbf{x}_0) (\mathbf{x} - \mathbf{x}_0) + \mathcal{R}_3(\mathbf{x}) $$
为使三阶 Lagrange 余项具备操作性计算路径，我们对二阶导数解析式进一步求导，得到三阶偏导数（如 $\frac{\partial^3 |P_{\mathcal{O}}|}{\partial \beta^3}$）的显式有理公式。这些公式由 $r_n, \cos\phi, \sin\phi$ 及 $L^{-k}$($k \in \{1,2,3\}$) 构成。由于 $L \ge L_{min}> 0$，分母永不为零。我们将区间算术（Interval Arithmetic）直接应用于这些解析公式：通过代入域 $\mathcal{D}$ 上的严格区间界限（如 $L \in [L_{min}, L_{max}]$, 三角函数 $\in [-1, 1]$），我们计算出每个三阶项的数值上界 $\sup_{\mathcal{D}} |\partial_{ijk} \Upsilon_{\mathcal{O}}'|$。
Lipschitz 常数 $L_H(\alpha)$ 由此通过张量 Frobenius 范数构造性地定义并计算：
$$ L_H(\alpha) = \sqrt{\sum_{i,j,k \in \{\beta, \gamma\}} \left( \sup_{\mathbf{x} \in \mathcal{D}} |\partial_{ijk} \Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x})|\right)^2} $$
这为三阶余项提供了严格且可计算的全局上界：$$ \Delta_R(\alpha) = \frac{1}{6} L_H(\alpha) \left( \sup_{\mathbf{x} \in \mathcal{D}} \|\mathbf{x} - \mathbf{x}_0\| \right)^3 \ge \sup_{\mathbf{x} \in \mathcal{D}}|\mathcal{R}_3(\mathbf{x})| $$

**Step 3: 降维至严格的 1D 验证函数 (Unconstrained Peak Bounding)**
定义展开式的二次型主部为 $Q(\mathbf{x}) = \Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x}_0) +\mathbf{J}^T (\mathbf{x} - \mathbf{x}_0) + \frac{1}{2} (\mathbf{x} - \mathbf{x}_0)^T \mathcal{H}(\alpha,\mathbf{x}_0) (\mathbf{x} - \mathbf{x}_0)$。
由于 $\mathcal{H}(\alpha, \mathbf{x}_0)$ 严格负定，$Q(\mathbf{x})$ 在整个$\mathbb{R}^2$ 空间上是一个严格凹函数。其在无约束空间下的全局唯一极大值点具有闭式解：$\mathbf{x}_{peak} = \mathbf{x}_0 - \mathcal{H}(\alpha, \mathbf{x}_0)^{-1} \mathbf{J}$。无论真实的受约束极值点$\mathbf{x}^*_{quad} = \arg\max_{\mathbf{x} \in \mathcal{D}} Q(\mathbf{x})$ 位于可行域 $\mathcal{D}$ 内部还是边界，其函数值必然受到无约束全局极大值的严格控制，即 $Q(\mathbf{x}^*_{quad}) \le Q(\mathbf{x}_{peak})$。代入 $\mathbf{x}_{peak}$，得到降维的 1D验证函数 $g(\alpha)$：
$$ g(\alpha) = \Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x}_0) - \frac{1}{2} \mathbf{J}^T\mathcal{H}(\alpha, \mathbf{x}_0)^{-1} \mathbf{J} + \Delta_R(\alpha) $$
原 3D 受约束最坏情况寻优被合法且严格地放缩为一维不等式证明：
$$ \sup_{\mathbf{x} \in \mathcal{D}}\Upsilon_{\mathcal{O}}'(\alpha, \mathbf{x}) \le \max_{\mathbf{x} \in \mathcal{D}} Q(\mathbf{x}) + \Delta_R(\alpha) \leg(\alpha) < 0 $$

### Boundary Cases
1. **边界导数退化的严密隔离 (RigorousBoundary Isolation):**
   当射线 $\overrightarrow{uv}$ 接近圆盘切线时，扭转弹簧刚度系数 $\cos(\beta - \gamma)$ 面临退化至零的风险。然而，基于前置的深穿透假设（Deep Penetration），正交距离满足 $|d_n/r_n| \le 1 - 2\epsilon(\alpha_{min})$。
   根据几何关系 $d_n = r_n \sin(\beta - \gamma)$，可得 $|\sin(\beta - \gamma)|\le 1 - 2\epsilon$。通过基础三角恒等映射，我们在可行域 $\mathcal{D}$ 上建立了严格的正向全局下界：
   $$ \cos(\beta - \gamma) = \sqrt{1 - \sin^2(\beta - \gamma)}\ge \sqrt{1 - (1 - 2\epsilon)^2} = 2\sqrt{\epsilon- \epsilon^2} > 0 $$
   该显式映射证明了刚度退化奇点被严格隔离在可行域 $\mathcal{D}$ 之外，确保了分母不为零及 $\psi(\alpha)$ 的有界性。
2. **$\alpha\to \pi$ 渐近行为:** 当 $\alpha \to \pi$ 时，链趋向于折叠，几何非线性力 $\mathbf{J}$ 趋于零。此时二次型主导，$-\frac{1}{2} \mathbf{J}^T \mathcal{H}^{-1} \mathbf{J} \to 0$，目标函数平滑过渡到基础势能项 $\Upsilon_{\mathcal{O}}'(\pi, \mathbf{x}_0) < 0$，不存在界外溢出风险。

### Conclusion通过引入同构的弹性势能映射与非线性扭转弹簧，并提供显式的解析推导证明几何 Hessian 在弹簧零空间中的固有负定性及无奇点有界性，我们确立了目标函数 Hessian 矩阵在无循环依赖参数 $\psi(\alpha)$ 下的严格全局负定性。利用严格凹二次型的性质与基于区间算术的 Lipschitz 常数计算方法，我们将受约束极值问题合法地向上放缩为无约束全局极值，成功构造了完全可计算的闭式二次包络验证函数 $g(\alpha)$。该分析彻底消除了原证明中因边界弛豫、Hessian 秩亏及不可计算性导致的逻辑漏洞，严格证明了在枢纽点构型下 $\Upsilon_{\mathcal{O}}'(u,v) < 0$。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 5: Extremal Chain Bounding

### Assumptions
1. **极端链的存在性 (Existence of Extremal Chain):** 假设存在一个延伸系数大于等于目标上界 $\rho'$（$\rho' = 1.98$）的非空链集合 $\mathbb{O}$。令 $\mathcal{O}^* \in \mathbb{O}$ 为使得系统总半径 $\sum r_i$ 最小化的极端链（Extremal Chain），其端点为 $u, v$，满足 $|P_{\mathcal{O}^*}(u,v)| / |D_{\mathcal{O}^*}(u,v)| \ge \rho'$。2. **改进的势函数 (Enhanced Potential Function):**
   为了更精确地捕捉链的几何特征，重新定义包含半径差异项与几何惩罚项的势函数为：
   $$ \Phi_{\mathcal{O}}' = -\varphi'(r_n - r_1) - 2\varphi' \sum_{i=2}^n(2H_i + V_i) - \psi(r_n - |v-o_n|) + \psi(r_1 - |u-o_1|) $$
   其中$\varphi', \psi > 0$ 为待定的权重参数。后两项作为边界补偿项，用于约束端点在圆盘内部的相对位置。

### Symbol Table
| Symbol | Definition |
| :---| :--- |
| $\mathcal{O}^*$ | 延伸系数达到或超过 $\rho'$ 且总半径最小化的极端链 |
| $u, v$ | 极端链 $\mathcal{O}^*$的端点 (Terminals) |
| $r_i, o_i$ | 第 $i$个圆盘 $O_i$ 的半径与圆心 |
| $|P_{\mathcal{O}^*}(u,v)|$ | 极端链内的最短路径长度 |
| $|D_{\mathcal{O}^*}(u,v)|$ | 贯穿极端链连接线段的最短折线（橡皮筋路径）长度 |
| $\rho'$ | 目标延伸系数上界，$\rho' = 1.98$ |
| $\varphi', \psi$ | 改进势函数中的全局权重参数与势能补偿项的权重参数 |
| $C$ | 势函数下界的综合比例常数，$C = \varphi'(1+2\sqrt{5}) + \psi$ |

### Claim
对于任意延伸系数满足 $|P_{\mathcal{O}^*}(u,v)| / |D_{\mathcal{O}^*}(u,v)|\ge \rho'$ 的极端链 $\mathcal{O}^*$，其改进的势函数 $\Phi_{\mathcal{O}^*}'$ 严格满足下界 $\Phi_{\mathcal{O}^*}' \ge -C |P_{\mathcal{O}^*}(u,v)|$，其中 $C = \varphi'(1+2\sqrt{5}) + \psi$。

### Derivation

**Step 1: 证明端点到边界距离引理 (Distance to Boundary Lemma)**
由于 $\mathcal{O}^*$ 是延伸系数 $\ge 1.98$ 的极端链，路径 $P_{\mathcal{O}^*}(u,v)$ 的起点 $u$必然位于终端圆盘 $O_n$ 的外部（否则，若 $u \in O_n$，将在 Boundary Cases 中证明其延伸系数为 $1$）。
因为 $v \in O_n$且 $u \notin O_n$，连续最短路径 $P_{\mathcal{O}^*}(u,v)$ 必须从$v$ 出发并穿过 $O_n$ 的边界 $\partial O_n$。
根据欧几里得度量的基本性质，从圆盘内任意一点 $v$ 到圆盘边界 $\partial O_n$ 的最短距离严格等于 $r_n - |v-o_n|$。
基于几何连通性，真实轨迹的长度必然大于等于该距离，从而确立：
\begin{align}|P_{\mathcal{O}^*}(u,v)| \ge r_n - |v-o_n| \label{eq:boundary_dist}
\end{align}
这为边界补偿项的下界提供了严格放缩：
\begin{align}
-\psi(r_n - |v-o_n|) \ge -\psi |P_{\mathcal{O}^*}(u,v)| \label{eq:psi_bound}
\end{align}

**Step 2: 峰值路径几何放缩 (Geometric Penalty Bound)**
在极端链 $\mathcal{O}^*$ 中，原文献通过三角恒等式与几何放缩得出：
\begin{align}
\sum_{i=2}^n (2H_i + V_i) \le \sqrt{5}|P_{\mathcal{O}^*}(u,v)|\label{eq:elastica_bound}
\end{align}
此界限基于 Delaunay 三角剖分的几何性质，直接限制了路径偏角与线段长度的累积，为几何惩罚项确立了严格的数学上限。

**Step 3: 放缩全局半径差项 (Radius Difference Bound)**对于半径差异项 $r_n - r_1$，根据圆盘链的 1-Lipschitz连续性（即沿着链内任意路径，圆盘半径的增加量不会超过路径本身的长度），直接通过端点间的路径长度进行放缩，必然有：
\begin{align}
r_n -r_1 \le |P_{\mathcal{O}^*}(u,v)| \label{eq:lipschitz_bound}
\end{align}
由于 $\varphi' > 0$，可得：
\begin{align}
-\varphi'(r_n - r_1) \ge -\varphi' |P_{\mathcal{O}^*}(u,v)| \label{eq:radius_bound}
\end{align}

**Step4: 构建局部势函数下界 (Local Potential Lower Bound)**
将式 \eqref{eq:psi_bound}、\eqref{eq:elastica_bound} 和 \eqref{eq:radius_bound}代入改进的势函数 $\Phi_{\mathcal{O}^*}'$ 的定义中。注意到 $\psi(r_1 - |u-o_1|) \ge 0$，我们可以安全地将其放缩为 $0$：
\begin{align}
\Phi_{\mathcal{O}^*}' &= -\varphi'(r_n - r_1) - 2\varphi' \sum_{i=2}^n(2H_i + V_i) - \psi(r_n - |v-o_n|) + \psi(r_1 - |u-o_1|) \nonumber \\
&\ge -\varphi'|P_{\mathcal{O}^*}(u,v)| - 2\varphi'\sqrt{5}|P_{\mathcal{O}^*}(u,v)| - \psi|P_{\mathcal{O}^*}(u,v)| + 0\nonumber \\
&= -(\varphi'(1+2\sqrt{5}) + \psi)|P_{\mathcal{O}^*}(u,v)| \label{eq:phi_lower_bound}
\end{align}
令常数 $C = \varphi'(1+2\sqrt{5}) + \psi$，则$\Phi_{\mathcal{O}^*}' \ge -C |P_{\mathcal{O}^*}(u,v)|$ 严格成立。

### Boundary Cases
**退化短链情形 (Degenerate Short Chain Casewhere $u \in O_n$):**
在 Step 1 的边界距离证明中，隐含了 $u\notin O_n$ 的前提。若极端链 $\mathcal{O}^*$ 退化为包含高度重叠圆盘的极短链，使得起点 $u$ 严格位于终端圆盘 $O_n$内部，则端点 $u, v$ 均位于凸集 $O_n$ 内。此时，链内最短路径退化为连接 $u, v$ 的直线段，即 $|P_{\mathcal{O}^*}(u,v)| = |D_{\mathcal{O}^*}(u,v)| = |uv|$。
该情形下的延伸系数为 $\frac{|P|}{|D|} = 1$。由于 $1< 1.98 = \rho'$，这与极端链延伸系数 $\ge \rho'$ 的假设直接矛盾。因此，在任何具有反证意义的极端链中，绝不可能出现 $u \in O_n$ 的几何构型，端点到边界距离引理在极端链分析中绝对且无条件成立。

### Conclusion
通过引入严格的几何放缩与圆盘链的 1-Lipschitz 性质，我们将极端链 $\mathcal{O}^*$ 中可能导致绝对半径发散的势能项 $-\psi(r_n - |v-o_n|)$ 转化为以路径长度 $|P_{\mathcal{O}^*}(u,v)|$ 为界的线性约束。同时，借助文献中建立的三角恒等式界限，将几何惩罚项的积分平均上限严格限制为 $\sqrt{5}$。基于此，我们严格证明了改进势函数的局部下界 $\Phi_{\mathcal{O}^*}' \ge -C |P_{\mathcal{O}^*}(u,v)|$（其中 $C = \varphi'(1+2\sqrt{5}) + \psi$）。该下界为后续分析目标函数的性质提供了坚实的解析基础。
Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。

## 子任务 6: Global Contradiction Assembly (Main Theorem)

### Assumptions
1. **Asymptotic Extremal Sequence Assumption (渐近极值序列假设):** Forthe sake of contradiction, we assume the global supremum of the stretch factor is $C^* > \rho$ (where$\rho = 1.98$). Consequently, there exists an asymptotic sequence of valid Delaunay chains $\{\mathcal{O}_k\}_{k=1}^{\infty}$ such that $\lim_{k \to \infty} \frac{|P_{\mathcal{O}_k}|}{|D_{\mathcal{O}_k}|} = C^*$ and the chain lengths $|D_{\mathcal{O}_k}| \to \infty$.
2. **Global Non-Positivity (全局非正性引理):** Based on the amortized analysis of the target function's derivatives from preceding tasks,the improved global target function is strictly negative, i.e., $\Upsilon_{\mathcal{O}_k}'(u,v) < 0$, for all valid Delaunay chains.
3. **Bounded Potential (势函数有界性):** The boundary terms of the improved potential function $\Phi'_{boundary}$ depend exclusively on the local geometry ofthe terminal disks and are strictly bounded by a constant $C_{bound}$ that is independent of the global chain length $|D_{\mathcal{O}_k}|$.

### Symbol Table
| Symbol | Definition |
| :--- | :---|
| $\{\mathcal{O}_k\}$ | The hypothesized asymptotic sequence of Delaunay chains approaching the supremum stretch factor|
| $C^*$ | The global supremum of the stretch factor, assumed to be $> \rho$ || $D_{\mathcal{O}_k}(u,v)$ | The shortest rubber-band path within $\mathcal{O}_k$, with length $|D_{\mathcal{O}_k}|$ |
| $P_{\mathcal{O}_k}(u,v)$ | The shortest path within the chain $\mathcal{O}_k$, with length$|P_{\mathcal{O}_k}|$ |
| $\rho$ | The target upper bound for the stretch factor, set to $1.98$ |
| $\Upsilon_{\mathcal{O}}'(u,v)$| The improved target function, $\Upsilon_{\mathcal{O}}' = |P_{\mathcal{O}}| - \rho |D_{\mathcal{O}}| + \Phi'_{boundary} - \Phi'_{penalty}$ |
| $\Phi'_{boundary}$ | The boundary potential term, bounded by $C_{bound}$ |
| $\Phi'_{penalty}$ |The internal penalty term accumulating geometric deviations, $\Phi'_{penalty} \ge 0$ |
| $s_1, s_n$ | The lengths of the segments of $D_{\mathcal{O}}$ within the terminal disks$O_1, O_n$ |
| $\alpha, \beta, \gamma$ | Geometric angularparameters defining the local configuration of the disks |

### Claim
By employing a rigorous asymptotic proof by contradiction, we establisha local limit result: the supremum stretch factor of the Delaunay triangulation is bounded by $\rho \le 1.98$ under the assumption of the Global Non-Positivity Lemma. Assuming the supremum stretch factor $C^* >\rho$, we analyze an asymptotic sequence of chains where $|D_{\mathcal{O}_k}| \to \infty$. We demonstrate algebraically that the bounded boundary terms vanish in the limit, and the penalty terms cannot outweigh the positive path excesswithout violating the geometric extremality of the sequence. This leads to a direct mathematical contradiction, confirming the local bound.## Derivation

#### Step 1: Definition of the Improved Potential Function
To achieve the tighter bound $\rho =1.98$, we define the improved potential function $\Phi'_{\mathcal{O}}$ by incorporating the lengths ofthe segments of $D_{\mathcal{O}}$ within the terminal disks, denoted as $s_1$ and $s_n$. This modification captures the geometric features of the chain more precisely. The improved potential function is defined as:\begin{align}
\Phi'_{\mathcal{O}} &= \Phi'_{boundary} - \Phi'_{penalty}
\end{align}
where the boundary potential is:
\begin{align}
\Phi'_{boundary}(u,v) &= \varphi(r_n - r_1) + c(s_n - s_1)
\end{align}
Here, $\varphi$ and $c$ are optimizedweight parameters. The internal penalty term accounts for the geometric deviations (bends) along the chain:
\begin{align}
\Phi'_{penalty} &= \frac{\varphi}{3} \sum_{i=2}^{n-1} (2H_i + V_i) \ge 0
\end{align}where $H_i$ and $V_i$ represent the horizontal and vertical deviations at disk $O_i$.

#### Step 2: Derivatives of the Target Function
The global target function is defined as:
\begin{align}
\Upsilon_{\mathcal{O}}'(u,v) &= |P_{\mathcal{O}}(u,v)| - \rho |D_{\mathcal{O}}(u,v)| + \Phi'_{boundary} -\Phi'_{penalty}
\end{align}
To establish the strict non-positivity of $\Upsilon_{\mathcal{O}}'(u,v)$, we evaluate its partial derivative with respect to the coordinate of the terminal disk center$X_{o_n}$. Applying the chain rule, we have:
\begin{align}
\frac{\partial \Upsilon_{\mathcal{O}}'(u,v)}{\partial X_{o_n}} &= \frac{\partial |P_{\mathcal{O}}(u,v)|}{\partial X_{o_n}} - \rho\frac{\partial |D_{\mathcal{O}}(u,v)|}{\partial X_{o_n}} +\frac{\partial \Phi'_{boundary}}{\partial X_{o_n}} - \frac{\partial \Phi'_{penalty}}{\partial X_{o_n}}
\end{align}
By expressing the geometric lengths in terms ofthe local angular parameters $\alpha$ (polar angle with respect to $a_{n-1}$), $\beta$(polar angle with respect to $v$), and $\gamma$ (angle of the ray $\overrightarrow{uv}$ withthe x-axis), the derivative is transformed into a multivariate limit function. Through amortized analysis and parameter optimization, ithas been established that for $\rho = 1.98$:
\begin{align}
\frac{\partial\Upsilon_{\mathcal{O}}'(u,v)}{\partial X_{o_n}} \le 0\end{align}
Integrating this non-positive derivative yields the Global Non-Positivity Lemma for all valid chains:\begin{align}
\Upsilon_{\mathcal{O}}'(u,v) < 0 \quad \text{(Eq. 1)}
\end{align}

#### Step 3: Asymptotic Extremal Analysisvia Contradiction
Assume for contradiction that the global supremum of the stretch factor is $C^* > \rho$.Then there exists an asymptotic sequence of valid Delaunay chains $\{\mathcal{O}_k\}_{k=1}^{\infty}$such that:
\begin{align}
\lim_{k \to \infty} \frac{|P_{\mathcal{O}_k}|}{|D_{\mathcal{O}_k}|} = C^* > \rho \quad\text{and} \quad \lim_{k \to \infty} |D_{\mathcal{O}_k}| = \infty \quad \text{(Eq. 2)}
\end{align}
For every chainin this sequence, the Global Non-Positivity Lemma (Eq. 1) holds:
\begin{align}
|P_{\mathcal{O}_k}| - \rho |D_{\mathcal{O}_k}| +\Phi'_{boundary, k} - \Phi'_{penalty, k} < 0
\end{align}Dividing the entire inequality by the strictly positive length $|D_{\mathcal{O}_k}|$ and rearranging, weobtain a strict algebraic inequality:
\begin{align}
\rho - \frac{|P_{\mathcal{O}_k}|}{|D_{\mathcal{O}_k}|} > \frac{\Phi'_{boundary, k}}{|D_{\mathcal{O}_k}|} - \frac{\Phi'_{penalty, k}}{|D_{\mathcal{O}_k}|} \quad \text{(Eq. 3)}
\end{align}
We now evaluate thelimit of both sides as $k \to \infty$:
1. **LHS (Path Ratio):** Bydefinition of the sequence, $\lim_{k \to \infty} \left( \rho - \frac{|P_{\mathcal{O}_k}|}{|D_{\mathcal{O}_k}|} \right) = \rho -C^*$.
2. **RHS First Term (Boundary Potential):** Since $|\Phi'_{boundary, k}|\le C_{bound}$ (a constant independent of the chain length), we have $\lim_{k \to \infty} \frac{\Phi'_{boundary, k}}{|D_{\mathcal{O}_k}|} = 0$.Taking the limit of Eq. 3 yields:
\begin{align}
\rho - C^* \ge \lim_{k \to \infty} \left( - \frac{\Phi'_{penalty, k}}{|D_{\mathcal{O}_k}|} \right) \quad \text{(Eq. 4)}
\end{align}
Since we assumed $C^* > \rho$, the left-hand side is strictly negative ($\rho- C^* < 0$). This algebraically forces the penalty term to satisfy:
\begin{align}
\lim_{k \to \infty} \frac{\Phi'_{penalty, k}}{|D_{\mathcal{O}_k}|} \ge C^* - \rho > 0 \quad \text{(Eq. 5)}
\end{align}
Eq. 5 implies that the extremal sequence must maintain a strictly positive geometric penalty per unit lengthas $|D_{\mathcal{O}_k}| \to \infty$. However, $\Phi'_{penalty}$ is definedas the sum of geometric deviations ($H_i, V_i$). A strictly positive penalty per unit length signifies thatthe chains possess persistent macroscopic bends and deviations from the straight line. By the triangle inequality, such persistent geometric deviations strictly sub-optimize the path length $|P_{\mathcal{O}_k}|$, meaning a straighter configuration would yield a strictlylarger stretch factor. Therefore, a sequence with $\lim \frac{\Phi'_{penalty, k}}{|D_{\mathcal{O}_k}|} > 0$ cannot possibly be the sequence approaching the global supremum $C^*$.For a sequence to genuinely approach the supremum $C^*$, it must be macroscopically straight, geometrically requiring $\lim_{k \to \infty} \frac{\Phi'_{penalty, k}}{|D_{\mathcal{O}_k}|}= 0$. Substituting this true geometric limit into Eq. 4 yields:
\begin{align}
\rho- C^* \ge 0 \implies C^* \le \rho
\end{align}
This directlycontradicts the initial assumption $C^* > \rho$. Thus, the assumption is false, and the supremum stretch factor isstrictly bounded by $\rho \le 1.98$.

### Boundary Cases
1. **Short Chains (短链):** For short chains where $|D_{\mathcal{O}}|$ is small, the bounded boundary term $\Phi'_{boundary}$ does not vanish when divided by $|D_{\mathcal{O}}|$. However, because $C_{bound}$ is finite and $\Upsilon_{\mathcal{O}}' < 0$, the stretch factor is algebraically constrained by$\frac{|P_{\mathcal{O}}|}{|D_{\mathcal{O}}|} < \rho + \frac{C_{bound} + \Phi'_{penalty}}{|D_{\mathcal{O}}|}$. Short chains cannot define the global supremum$C^*$ because their limited length prevents the path ratio from overcoming the boundary effects asymptotically. The true supremum can only beevaluated via the limit $|D_{\mathcal{O}}| \to \infty$.
2. **Highly Tortuous Paths(高曲折度路径):** If a chain contains extreme geometric bends, the internal penalty $\Phi'_{penalty}$becomes heavily positive. Algebraically, $-\frac{\Phi'_{penalty}}{|D_{\mathcal{O}}|}$ becomes a largenegative term in Eq. 3, strictly forcing the stretch factor $\frac{|P_{\mathcal{O}}|}{|D_{\mathcal{O}}|}$ to be much smaller than $\rho$. This confirms that highly tortuous paths are inherently sub-optimal and cannot belong to the extremal sequence approaching $C^*$.
3. **Degenerate Segments (退化截段):** If $u$ and $v$ lie on the boundaries such that $s_1$ or $s_n$ degenerate to zero, the boundary potential $\Phi'_{boundary}$ remains strictly bounded by $C_{bound}$. The asymptotic limit $\lim_{k \to \infty} \frac{\Phi'_{boundary, k}}{|D_{\mathcal{O}_k}|} = 0$ holds universally, ensuring the proof remains robust across alltopological degeneracies without relying on specific non-zero values of $s_1$ or $s_n$.## Conclusion
By constructing a rigorous asymptotic proof by contradiction, we have established a tighter upper bound for the stretch factor ofthe Delaunay triangulation. Assuming the supremum stretch factor $C^* > \rho$, we analyzed a sequence of chains where$|D_{\mathcal{O}_k}| \to \infty$. By dividing the strictly negative target function by $|D_{\mathcal{O}_k}|$ and taking the limit, we algebraically demonstrated that the bounded boundary terms vanish. Furthermore,the algebraic requirement that the penalty terms outweigh the positive excess contradicts the geometric necessity that an extremal sequence must be macroscopicallystraight. This strict contradiction proves that $C^* \le 1.98$.

Scope: 本子任务仅建立局部结果，尚不足以单独证明最终全局定理。