"""IFC4 extruded-box export with assembly-order properties."""
import logging
import math
from pathlib import Path
from masonmind.config import load_config
from masonmind.schemas import Design, AssemblySequence


def export_ifc(design: Design, sequence: AssemblySequence, path: Path, config: dict | None = None) -> Path | None:
    """Export geometric BIM in metres, or explicitly warn if dependency is absent."""
    try:
        import ifcopenshell
        import ifcopenshell.guid
    except ImportError:
        logging.warning("IfcOpenShell unavailable: IFC export skipped")
        return None
    file = ifcopenshell.file(schema="IFC4")
    guid = ifcopenshell.guid.new
    def axis(position: tuple, yaw: float = 0):
        return file.create_entity("IfcAxis2Placement3D", Location=file.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in position)),
            Axis=file.create_entity("IfcDirection", DirectionRatios=(0., 0., 1.)),
            RefDirection=file.create_entity("IfcDirection", DirectionRatios=(math.cos(yaw), math.sin(yaw), 0.)))
    context = file.create_entity("IfcGeometricRepresentationContext", ContextIdentifier="Body", ContextType="Model",
        CoordinateSpaceDimension=3, Precision=1e-6, WorldCoordinateSystem=axis((0, 0, 0)))
    units = file.create_entity("IfcUnitAssignment", Units=[file.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")])
    project = file.create_entity("IfcProject", GlobalId=guid(), Name="MasonMind", RepresentationContexts=[context], UnitsInContext=units)
    site = file.create_entity("IfcSite", GlobalId=guid(), Name="Simulation site")
    building = file.create_entity("IfcBuilding", GlobalId=guid(), Name="Parametric wall")
    storey = file.create_entity("IfcBuildingStorey", GlobalId=guid(), Name="Assembly")
    for parent, child in ((project, site), (site, building), (building, storey)):
        file.create_entity("IfcRelAggregates", GlobalId=guid(), RelatingObject=parent, RelatedObjects=[child])
    length, width, height = (config or load_config())["geometry"]["brick_size"]
    elements = []
    order = {name: index for index, name in enumerate(sequence.order)}
    for brick in design.bricks:
        if brick.is_opening:
            continue
        profile = file.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=length, YDim=width)
        solid = file.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=axis((0, 0, -height / 2)),
            ExtrudedDirection=file.create_entity("IfcDirection", DirectionRatios=(0., 0., 1.)), Depth=height)
        shape = file.create_entity("IfcShapeRepresentation", ContextOfItems=context, RepresentationIdentifier="Body",
            RepresentationType="SweptSolid", Items=[solid])
        element = file.create_entity("IfcBuildingElementProxy", GlobalId=guid(), Name=brick.id,
            ObjectPlacement=file.create_entity("IfcLocalPlacement", RelativePlacement=axis((brick.x, brick.y, brick.z), math.radians(brick.yaw_deg))),
            Representation=file.create_entity("IfcProductDefinitionShape", Representations=[shape]))
        prop = file.create_entity("IfcPropertySingleValue", Name="AssemblyOrder", NominalValue=file.create_entity("IfcInteger", order[brick.id]))
        pset = file.create_entity("IfcPropertySet", GlobalId=guid(), Name="MasonMindAssembly", HasProperties=[prop])
        file.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[element], RelatingPropertyDefinition=pset)
        elements.append(element)
    file.create_entity("IfcRelContainedInSpatialStructure", GlobalId=guid(), RelatedElements=elements, RelatingStructure=storey)
    path.parent.mkdir(parents=True, exist_ok=True)
    file.write(str(path))
    return path
