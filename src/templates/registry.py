"""OpenFOAM case templates registry and management."""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import json


@dataclass
class ParameterDefinition:
    """Definition of a template parameter with user-friendly prompts."""
    name: str
    label: str  # User-friendly label in Chinese
    description: str  # Detailed description for users
    type: str  # "float", "int", "string", "choice"
    unit: Optional[str] = None
    default: Any = None
    range: Optional[tuple] = None  # (min, max) for numeric types
    choices: Optional[List[str]] = None  # For choice type
    hint: str = ""  # Example or additional guidance
    required: bool = True

    def format_prompt(self) -> str:
        """Format parameter as a user-friendly prompt."""
        lines = [f"📌 {self.label}"]
        lines.append(f"   {self.description}")

        if self.unit:
            lines.append(f"   单位: {self.unit}")

        if self.range:
            lines.append(f"   范围: {self.range[0]} - {self.range[1]}")

        if self.default is not None:
            lines.append(f"   默认值: {self.default}")

        if self.hint:
            lines.append(f"   示例: {self.hint}")

        if self.choices:
            lines.append(f"   可选: {', '.join(self.choices)}")

        return "\n".join(lines)


@dataclass
class CaseTemplate:
    """OpenFOAM case template definition."""
    id: str
    name: str  # Chinese name
    description: str
    category: str  # "incompressible", "compressible", "heat_transfer"
    solver: str
    geometry_type: str
    parameters: List[ParameterDefinition]
    is_2d: bool = False
    supports_turbulence: bool = True

    def get_required_parameters(self) -> List[ParameterDefinition]:
        """Get list of required parameters."""
        return [p for p in self.parameters if p.required]

    def get_optional_parameters(self) -> List[ParameterDefinition]:
        """Get list of optional parameters."""
        return [p for p in self.parameters if not p.required]

    def validate_parameters(self, values: Dict[str, Any]) -> List[str]:
        """Validate parameter values, return list of errors."""
        errors = []
        for param in self.parameters:
            if param.required and param.name not in values:
                errors.append(f"缺少必需参数: {param.label}")
                continue

            if param.name in values:
                value = values[param.name]
                if param.range:
                    if value < param.range[0] or value > param.range[1]:
                        errors.append(
                            f"{param.label} 超出范围: {value} "
                            f"(应在 {param.range[0]} - {param.range[1]} 之间)"
                        )
                if param.choices and value not in param.choices:
                    errors.append(
                        f"{param.label} 无效选项: {value} "
                        f"(可选: {', '.join(param.choices)})"
                    )
        return errors


# ============================================================================
# Template Definitions
# ============================================================================

PIPE_FLOW_TEMPLATE = CaseTemplate(
    id="pipe_flow",
    name="管道流动",
    description="圆管或方管内的流动模拟，适用于内流分析",
    category="incompressible",
    solver="simpleFoam",
    geometry_type="pipe",
    is_2d=True,
    parameters=[
        ParameterDefinition(
            name="diameter",
            label="管道直径",
            description="管道的内径尺寸",
            type="float",
            unit="m",
            default=0.1,
            range=(0.001, 10.0),
            hint="0.05 表示 5 厘米"
        ),
        ParameterDefinition(
            name="length",
            label="管道长度",
            description="管道的轴向长度，建议至少为直径的10倍以获得充分发展流动",
            type="float",
            unit="m",
            default=1.0,
            range=(0.01, 100.0),
            hint="1.0 表示 1 米"
        ),
        ParameterDefinition(
            name="inlet_velocity",
            label="入口流速",
            description="流体进入管道的平均速度",
            type="float",
            unit="m/s",
            default=1.0,
            range=(0.001, 100.0),
            hint="1.0 表示 1 米每秒"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["water", "air", "oil"],
            default="water",
            hint="water=水, air=空气, oil=机油"
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)

CAVITY_FLOW_TEMPLATE = CaseTemplate(
    id="cavity_flow",
    name="顶盖驱动方腔流",
    description="经典的顶盖驱动方腔流动问题，用于验证和基准测试",
    category="incompressible",
    solver="icoFoam",
    geometry_type="cavity",
    is_2d=True,
    parameters=[
        ParameterDefinition(
            name="width",
            label="方腔宽度",
            description="方腔的水平尺寸",
            type="float",
            unit="m",
            default=0.1,
            range=(0.001, 10.0),
            hint="0.1 表示 10 厘米"
        ),
        ParameterDefinition(
            name="height",
            label="方腔高度",
            description="方腔的垂直尺寸，通常与宽度相等",
            type="float",
            unit="m",
            default=0.1,
            range=(0.001, 10.0),
            hint="0.1 表示 10 厘米"
        ),
        ParameterDefinition(
            name="lid_velocity",
            label="顶盖速度",
            description="顶盖（移动壁面）的移动速度",
            type="float",
            unit="m/s",
            default=1.0,
            range=(0.001, 100.0),
            hint="1.0 表示 1 米每秒"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["water", "air", "oil"],
            default="water",
            hint="water=水, air=空气, oil=机油"
        ),
        ParameterDefinition(
            name="end_time",
            label="模拟结束时间",
            description="瞬态模拟的总时长",
            type="float",
            unit="s",
            default=1.0,
            range=(0.001, 1000.0),
            hint="1.0 表示模拟 1 秒",
            required=False
        ),
    ]
)

CYLINDER_FLOW_TEMPLATE = CaseTemplate(
    id="cylinder_flow",
    name="圆柱绕流",
    description="二维圆柱绕流问题，可观察卡门涡街",
    category="incompressible",
    solver="pimpleFoam",
    geometry_type="external_flow",
    is_2d=True,
    parameters=[
        ParameterDefinition(
            name="cylinder_diameter",
            label="圆柱直径",
            description="圆柱的直径",
            type="float",
            unit="m",
            default=0.1,
            range=(0.001, 10.0),
            hint="0.1 表示 10 厘米"
        ),
        ParameterDefinition(
            name="inlet_velocity",
            label="来流速度",
            description="远场来流的速度",
            type="float",
            unit="m/s",
            default=1.0,
            range=(0.001, 100.0),
            hint="1.0 表示 1 米每秒"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["water", "air"],
            default="air",
            hint="water=水, air=空气"
        ),
        ParameterDefinition(
            name="domain_length",
            label="计算域长度",
            description="计算域的流向长度（相对于圆柱直径的倍数）",
            type="float",
            unit="倍",
            default=20.0,
            range=(10.0, 50.0),
            hint="20 表示计算域长度为圆柱直径的 20 倍",
            required=False
        ),
        ParameterDefinition(
            name="end_time",
            label="模拟结束时间",
            description="瞬态模拟的总时长",
            type="float",
            unit="s",
            default=10.0,
            range=(0.1, 1000.0),
            hint="10.0 表示模拟 10 秒",
            required=False
        ),
    ]
)

HEAT_TRANSFER_TEMPLATE = CaseTemplate(
    id="natural_convection",
    name="自然对流换热",
    description="封闭腔体内的自然对流传热问题",
    category="heat_transfer",
    solver="buoyantSimpleFoam",
    geometry_type="cavity",
    is_2d=True,
    supports_turbulence=True,
    parameters=[
        ParameterDefinition(
            name="width",
            label="腔体宽度",
            description="腔体的水平尺寸",
            type="float",
            unit="m",
            default=0.1,
            range=(0.001, 10.0),
            hint="0.1 表示 10 厘米"
        ),
        ParameterDefinition(
            name="height",
            label="腔体高度",
            description="腔体的垂直尺寸",
            type="float",
            unit="m",
            default=0.1,
            range=(0.001, 10.0),
            hint="0.1 表示 10 厘米"
        ),
        ParameterDefinition(
            name="hot_wall_temp",
            label="热壁面温度",
            description="左侧壁面的温度",
            type="float",
            unit="K",
            default=310.0,
            range=(273.0, 1000.0),
            hint="310 表示 310 开尔文（约 37°C）"
        ),
        ParameterDefinition(
            name="cold_wall_temp",
            label="冷壁面温度",
            description="右侧壁面的温度",
            type="float",
            unit="K",
            default=290.0,
            range=(200.0, 500.0),
            hint="290 表示 290 开尔文（约 17°C）"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["air", "water"],
            default="air",
            hint="air=空气, water=水"
        ),
    ]
)


# ============================================================================
# Multiphase Templates
# ============================================================================

DAM_BREAK_TEMPLATE = CaseTemplate(
    id="dam_break",
    name="溃坝（VOF两相流）",
    description="经典溃坝问题，水柱坍塌后撞击障碍物，使用VOF方法捕捉自由表面",
    category="multiphase",
    solver="interFoam",
    geometry_type="tank",
    is_2d=True,
    supports_turbulence=False,
    parameters=[
        ParameterDefinition(
            name="tank_width",
            label="水槽宽度",
            description="水槽的水平尺寸",
            type="float",
            unit="m",
            default=0.584,
            range=(0.1, 10.0),
            hint="0.584 是OpenFOAM官方教程尺寸"
        ),
        ParameterDefinition(
            name="tank_height",
            label="水槽高度",
            description="水槽的垂直尺寸",
            type="float",
            unit="m",
            default=0.584,
            range=(0.1, 10.0),
            hint="0.584 是OpenFOAM官方教程尺寸"
        ),
        ParameterDefinition(
            name="water_column_width",
            label="水柱宽度",
            description="初始水柱的宽度（从左壁开始）",
            type="float",
            unit="m",
            default=0.146,
            range=(0.01, 5.0),
            hint="通常为水槽宽度的1/4"
        ),
        ParameterDefinition(
            name="water_column_height",
            label="水柱高度",
            description="初始水柱的高度",
            type="float",
            unit="m",
            default=0.292,
            range=(0.01, 5.0),
            hint="通常为水槽高度的1/2"
        ),
        ParameterDefinition(
            name="end_time",
            label="模拟结束时间",
            description="模拟总时长",
            type="float",
            unit="s",
            default=1.0,
            range=(0.1, 100.0),
            hint="1.0 秒通常足以观察到主要流动特征",
            required=False
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)

BUBBLE_RISING_TEMPLATE = CaseTemplate(
    id="bubble_rising",
    name="气泡上升（VOF两相流）",
    description="单气泡在液体中上升问题，使用VOF方法，包含表面张力效应",
    category="multiphase",
    solver="interFoam",
    geometry_type="column",
    is_2d=True,
    supports_turbulence=False,
    parameters=[
        ParameterDefinition(
            name="bubble_diameter",
            label="气泡直径",
            description="初始气泡的直径",
            type="float",
            unit="m",
            default=0.01,
            range=(0.001, 0.5),
            hint="0.01 表示 1 厘米"
        ),
        ParameterDefinition(
            name="column_width",
            label="液柱宽度",
            description="计算域的宽度，建议至少为气泡直径的6倍",
            type="float",
            unit="m",
            default=0.06,
            range=(0.01, 2.0),
            hint="0.06 表示 6 厘米"
        ),
        ParameterDefinition(
            name="column_height",
            label="液柱高度",
            description="计算域的高度，建议至少为气泡直径的12倍",
            type="float",
            unit="m",
            default=0.12,
            range=(0.01, 5.0),
            hint="0.12 表示 12 厘米"
        ),
        ParameterDefinition(
            name="liquid",
            label="液体类型",
            description="选择液体",
            type="choice",
            choices=["water", "oil"],
            default="water",
            hint="water=水, oil=机油"
        ),
        ParameterDefinition(
            name="surface_tension",
            label="表面张力",
            description="气液界面的表面张力系数",
            type="float",
            unit="N/m",
            default=0.07,
            range=(0.001, 1.0),
            hint="水-空气界面约 0.07 N/m",
            required=False
        ),
        ParameterDefinition(
            name="end_time",
            label="模拟结束时间",
            description="模拟总时长",
            type="float",
            unit="s",
            default=1.0,
            range=(0.1, 100.0),
            hint="取决于气泡上升速度",
            required=False
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)


# ============================================================================
# Additional Incompressible Templates
# ============================================================================

BACKWARD_STEP_TEMPLATE = CaseTemplate(
    id="backward_step",
    name="后台阶流",
    description="后台阶流动分离与再附着问题，经典CFD验证案例",
    category="incompressible",
    solver="simpleFoam",
    geometry_type="step",
    is_2d=True,
    supports_turbulence=True,
    parameters=[
        ParameterDefinition(
            name="step_height",
            label="台阶高度",
            description="台阶的高度 H",
            type="float",
            unit="m",
            default=0.0127,
            range=(0.001, 1.0),
            hint="0.0127 (Driver & Seegmiller 实验值)"
        ),
        ParameterDefinition(
            name="inlet_height",
            label="入口高度",
            description="台阶上游通道高度",
            type="float",
            unit="m",
            default=0.0127,
            range=(0.001, 1.0),
            hint="通常等于台阶高度（膨胀比 = 2）"
        ),
        ParameterDefinition(
            name="outlet_length",
            label="出口段长度",
            description="台阶下游通道长度（台阶高度的倍数）",
            type="float",
            unit="倍",
            default=30.0,
            range=(10.0, 60.0),
            hint="30H 以确保充分发展和再附着"
        ),
        ParameterDefinition(
            name="inlet_velocity",
            label="入口流速",
            description="入口平均流速",
            type="float",
            unit="m/s",
            default=44.2,
            range=(0.01, 500.0),
            hint="44.2 m/s (Driver & Seegmiller Re_H=36000)"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["air", "water"],
            default="air",
            hint="air=空气, water=水"
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)

CHANNEL_FLOW_TEMPLATE = CaseTemplate(
    id="channel_flow",
    name="周期通道流",
    description="全发展湍流通道流，使用周期边界条件和体积力驱动",
    category="incompressible",
    solver="pimpleFoam",
    geometry_type="channel",
    is_2d=False,
    supports_turbulence=True,
    parameters=[
        ParameterDefinition(
            name="half_height",
            label="通道半高",
            description="通道半高 delta，通道全高 = 2*delta",
            type="float",
            unit="m",
            default=1.0,
            range=(0.001, 10.0),
            hint="1.0 表示无量纲化通道 (DNS/LES 通常用 delta=1)"
        ),
        ParameterDefinition(
            name="length",
            label="流向长度",
            description="流向计算域长度（pi*delta 的倍数）",
            type="float",
            unit="m",
            default=6.283,
            range=(0.1, 100.0),
            hint="2*pi*delta = 6.283 (标准设置)"
        ),
        ParameterDefinition(
            name="span",
            label="展向宽度",
            description="展向计算域宽度（pi*delta 的倍数）",
            type="float",
            unit="m",
            default=3.142,
            range=(0.1, 50.0),
            hint="pi*delta = 3.142 (标准设置)"
        ),
        ParameterDefinition(
            name="re_tau",
            label="摩擦雷诺数",
            description="Re_tau = u_tau * delta / nu",
            type="float",
            unit="-",
            default=395.0,
            range=(100.0, 5200.0),
            hint="395 (经典 channel395 案例), 180 (最低DNS分辨率)"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["air", "water"],
            default="air",
            hint="air=空气, water=水"
        ),
        ParameterDefinition(
            name="end_time",
            label="模拟结束时间",
            description="模拟总时长",
            type="float",
            unit="s",
            default=100.0,
            range=(1.0, 10000.0),
            hint="需要足够长的时间达到统计稳态",
            required=False
        ),
    ]
)

HEAT_EXCHANGER_TEMPLATE = CaseTemplate(
    id="heat_exchanger",
    name="管道换热（强制对流）",
    description="带壁面加热的管道流动，强制对流换热问题",
    category="heat_transfer",
    solver="buoyantSimpleFoam",
    geometry_type="pipe",
    is_2d=True,
    supports_turbulence=True,
    parameters=[
        ParameterDefinition(
            name="diameter",
            label="管道直径",
            description="管道的内径尺寸",
            type="float",
            unit="m",
            default=0.05,
            range=(0.001, 10.0),
            hint="0.05 表示 5 厘米"
        ),
        ParameterDefinition(
            name="length",
            label="管道长度",
            description="管道的轴向长度",
            type="float",
            unit="m",
            default=1.0,
            range=(0.01, 100.0),
            hint="1.0 表示 1 米"
        ),
        ParameterDefinition(
            name="inlet_velocity",
            label="入口流速",
            description="流体进入管道的平均速度",
            type="float",
            unit="m/s",
            default=1.0,
            range=(0.01, 100.0),
            hint="1.0 表示 1 米每秒"
        ),
        ParameterDefinition(
            name="inlet_temperature",
            label="入口温度",
            description="流体入口温度",
            type="float",
            unit="K",
            default=300.0,
            range=(200.0, 1000.0),
            hint="300 K (约 27°C)"
        ),
        ParameterDefinition(
            name="wall_temperature",
            label="壁面温度",
            description="管道壁面的恒温温度",
            type="float",
            unit="K",
            default=350.0,
            range=(200.0, 1500.0),
            hint="350 K (约 77°C)"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["air", "water"],
            default="air",
            hint="air=空气, water=水"
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)


FLAT_PLATE_TEMPLATE = CaseTemplate(
    id="flat_plate",
    name="平板边界层",
    description="平板湍流边界层发展问题，经典Blasius验证案例，适合y+调试",
    category="incompressible",
    solver="simpleFoam",
    geometry_type="plate",
    is_2d=True,
    supports_turbulence=True,
    parameters=[
        ParameterDefinition(
            name="plate_length",
            label="平板长度",
            description="平板的流向长度",
            type="float",
            unit="m",
            default=1.0,
            range=(0.01, 100.0),
            hint="1.0 表示 1 米"
        ),
        ParameterDefinition(
            name="domain_height",
            label="计算域高度",
            description="垂直于平板方向的计算域高度",
            type="float",
            unit="m",
            default=0.1,
            range=(0.01, 10.0),
            hint="建议为平板长度的0.1倍或边界层厚度的10倍"
        ),
        ParameterDefinition(
            name="inlet_velocity",
            label="来流速度",
            description="入口自由流速度",
            type="float",
            unit="m/s",
            default=10.0,
            range=(0.1, 500.0),
            hint="10.0 m/s 对应 Re_x ≈ 6.7e5 (空气，1m处)"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["air", "water"],
            default="air",
            hint="air=空气, water=水"
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
        ParameterDefinition(
            name="target_yplus",
            label="目标y+",
            description="壁面第一层网格的目标y+值",
            type="float",
            unit="-",
            default=30.0,
            range=(0.1, 300.0),
            hint="30-300 壁面函数, <1 低雷诺数模型",
            required=False
        ),
    ]
)

MIXING_ELBOW_TEMPLATE = CaseTemplate(
    id="mixing_elbow",
    name="弯管混合流",
    description="90度弯管流动问题，经典ANSYS Fluent验证案例，包含二次流和分离",
    category="incompressible",
    solver="simpleFoam",
    geometry_type="elbow",
    is_2d=False,
    supports_turbulence=True,
    parameters=[
        ParameterDefinition(
            name="pipe_diameter",
            label="管道直径",
            description="管道内径",
            type="float",
            unit="m",
            default=0.1,
            range=(0.01, 10.0),
            hint="0.1 表示 10 厘米"
        ),
        ParameterDefinition(
            name="bend_radius",
            label="弯曲半径",
            description="弯管中心线的曲率半径",
            type="float",
            unit="m",
            default=0.15,
            range=(0.01, 20.0),
            hint="通常为管径的1.5-3倍"
        ),
        ParameterDefinition(
            name="inlet_length",
            label="入口段长度",
            description="弯头上游直管段长度",
            type="float",
            unit="m",
            default=0.5,
            range=(0.1, 50.0),
            hint="建议至少5倍管径以获得充分发展流动"
        ),
        ParameterDefinition(
            name="outlet_length",
            label="出口段长度",
            description="弯头下游直管段长度",
            type="float",
            unit="m",
            default=0.5,
            range=(0.1, 50.0),
            hint="建议至少10倍管径"
        ),
        ParameterDefinition(
            name="inlet_velocity",
            label="入口流速",
            description="流体进入管道的平均速度",
            type="float",
            unit="m/s",
            default=1.0,
            range=(0.01, 100.0),
            hint="1.0 表示 1 米每秒"
        ),
        ParameterDefinition(
            name="fluid",
            label="流体类型",
            description="选择工作流体",
            type="choice",
            choices=["water", "air"],
            default="water",
            hint="water=水, air=空气"
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)


# ============================================================================
# Compressible Flow Templates
# ============================================================================

SHOCK_TUBE_TEMPLATE = CaseTemplate(
    id="shock_tube",
    name="激波管（Sod问题）",
    description="经典Sod激波管问题，一维可压缩流验证案例，包含激波、膨胀波和接触间断",
    category="compressible",
    solver="rhoCentralFoam",
    geometry_type="tube",
    is_2d=True,
    supports_turbulence=False,
    parameters=[
        ParameterDefinition(
            name="tube_length",
            label="管道长度",
            description="激波管总长度",
            type="float",
            unit="m",
            default=10.0,
            range=(0.1, 100.0),
            hint="10.0 是标准Sod问题长度"
        ),
        ParameterDefinition(
            name="diaphragm_position",
            label="隔膜位置",
            description="初始隔膜位置（从左端算起）",
            type="float",
            unit="m",
            default=5.0,
            range=(0.01, 99.0),
            hint="5.0 表示在中间位置"
        ),
        ParameterDefinition(
            name="left_pressure",
            label="左侧压力",
            description="隔膜左侧初始压力",
            type="float",
            unit="Pa",
            default=100000.0,
            range=(1000.0, 1e8),
            hint="100000 Pa = 1 bar（高压侧）"
        ),
        ParameterDefinition(
            name="right_pressure",
            label="右侧压力",
            description="隔膜右侧初始压力",
            type="float",
            unit="Pa",
            default=10000.0,
            range=(100.0, 1e7),
            hint="10000 Pa = 0.1 bar（低压侧）"
        ),
        ParameterDefinition(
            name="left_temperature",
            label="左侧温度",
            description="隔膜左侧初始温度",
            type="float",
            unit="K",
            default=348.432,
            range=(100.0, 5000.0),
            hint="348.432 K（标准Sod问题）"
        ),
        ParameterDefinition(
            name="right_temperature",
            label="右侧温度",
            description="隔膜右侧初始温度",
            type="float",
            unit="K",
            default=278.746,
            range=(100.0, 5000.0),
            hint="278.746 K（标准Sod问题）"
        ),
        ParameterDefinition(
            name="end_time",
            label="模拟结束时间",
            description="模拟总时长",
            type="float",
            unit="s",
            default=0.007,
            range=(0.0001, 10.0),
            hint="0.007 秒（标准Sod问题）",
            required=False
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)

SUPERSONIC_NOZZLE_TEMPLATE = CaseTemplate(
    id="supersonic_nozzle",
    name="超音速喷管",
    description="收缩-扩张喷管（拉瓦尔喷管）流动问题，用于研究可压缩流从亚音速到超音速的加速过程",
    category="compressible",
    solver="rhoCentralFoam",
    geometry_type="nozzle",
    is_2d=True,
    supports_turbulence=False,
    parameters=[
        ParameterDefinition(
            name="inlet_area",
            label="入口面积",
            description="喷管入口截面积",
            type="float",
            unit="m²",
            default=0.001,
            range=(1e-6, 10.0),
            hint="0.001 m² (入口直径约 3.6 cm)"
        ),
        ParameterDefinition(
            name="throat_area",
            label="喉部面积",
            description="喷管喉部（最小）截面积",
            type="float",
            unit="m²",
            default=0.0005,
            range=(1e-7, 5.0),
            hint="0.0005 m² (喉部直径约 2.5 cm)"
        ),
        ParameterDefinition(
            name="outlet_area",
            label="出口面积",
            description="喷管出口截面积",
            type="float",
            unit="m²",
            default=0.00125,
            range=(1e-6, 10.0),
            hint="面积比 = outlet/throat 决定设计马赫数"
        ),
        ParameterDefinition(
            name="length",
            label="喷管长度",
            description="喷管总长度",
            type="float",
            unit="m",
            default=0.3,
            range=(0.01, 50.0),
            hint="0.3 表示 30 厘米"
        ),
        ParameterDefinition(
            name="inlet_pressure",
            label="入口总压",
            description="入口滞止压力",
            type="float",
            unit="Pa",
            default=300000.0,
            range=(10000.0, 1e8),
            hint="300000 Pa = 3 bar"
        ),
        ParameterDefinition(
            name="inlet_temperature",
            label="入口总温",
            description="入口滞止温度",
            type="float",
            unit="K",
            default=300.0,
            range=(100.0, 3000.0),
            hint="300 K (约 27°C)"
        ),
        ParameterDefinition(
            name="outlet_pressure",
            label="出口压力",
            description="出口静压（反压）",
            type="float",
            unit="Pa",
            default=100000.0,
            range=(1000.0, 1e7),
            hint="100000 Pa = 1 bar（环境压力）"
        ),
        ParameterDefinition(
            name="end_time",
            label="模拟结束时间",
            description="模拟总时长",
            type="float",
            unit="s",
            default=0.01,
            range=(0.0001, 100.0),
            hint="0.01 秒",
            required=False
        ),
        ParameterDefinition(
            name="mesh_density",
            label="网格密度",
            description="网格精细程度",
            type="choice",
            choices=["coarse", "medium", "fine"],
            default="medium",
            hint="coarse=粗, medium=中等, fine=精细",
            required=False
        ),
    ]
)


# Template registry
TEMPLATES: Dict[str, CaseTemplate] = {
    "pipe_flow": PIPE_FLOW_TEMPLATE,
    "cavity_flow": CAVITY_FLOW_TEMPLATE,
    "cylinder_flow": CYLINDER_FLOW_TEMPLATE,
    "natural_convection": HEAT_TRANSFER_TEMPLATE,
    # Multiphase templates
    "dam_break": DAM_BREAK_TEMPLATE,
    "bubble_rising": BUBBLE_RISING_TEMPLATE,
    # Additional incompressible templates
    "backward_step": BACKWARD_STEP_TEMPLATE,
    "channel_flow": CHANNEL_FLOW_TEMPLATE,
    "flat_plate": FLAT_PLATE_TEMPLATE,
    "mixing_elbow": MIXING_ELBOW_TEMPLATE,
    # Additional heat transfer templates
    "heat_exchanger": HEAT_EXCHANGER_TEMPLATE,
    # Compressible flow templates
    "shock_tube": SHOCK_TUBE_TEMPLATE,
    "supersonic_nozzle": SUPERSONIC_NOZZLE_TEMPLATE,
}


def get_template(template_id: str) -> Optional[CaseTemplate]:
    """Get a template by ID."""
    return TEMPLATES.get(template_id)


def list_templates(category: Optional[str] = None) -> List[CaseTemplate]:
    """List all templates, optionally filtered by category."""
    templates = list(TEMPLATES.values())
    if category:
        templates = [t for t in templates if t.category == category]
    return templates


def get_template_summary(template: CaseTemplate) -> str:
    """Get a user-friendly summary of a template."""
    lines = [
        f"## {template.name} ({template.id})",
        f"",
        f"{template.description}",
        f"",
        f"- 类别: {template.category}",
        f"- 求解器: {template.solver}",
        f"- 2D/3D: {'2D' if template.is_2d else '3D'}",
        f"",
        f"### 必需参数:",
    ]

    for param in template.get_required_parameters():
        lines.append(f"- {param.label} ({param.name}): {param.description}")
        if param.unit:
            lines.append(f"  单位: {param.unit}")
        if param.default is not None:
            lines.append(f"  默认: {param.default}")

    optional = template.get_optional_parameters()
    if optional:
        lines.append("")
        lines.append("### 可选参数:")
        for param in optional:
            lines.append(f"- {param.label} ({param.name}): {param.description}")

    return "\n".join(lines)
