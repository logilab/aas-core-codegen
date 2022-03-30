"""Generate XML Schema Definition (XSD) corresponding to the meta-model."""

import re
import xml.etree.ElementTree as ET

# noinspection PyUnresolvedReferences
import xml.dom.minidom
from typing import TextIO, MutableMapping, Optional, Tuple, List, Sequence

from icontract import ensure

import aas_core_codegen.xsd
from aas_core_codegen import (
    naming,
    specific_implementations,
    intermediate,
    run,
    infer_for_schema,
)
from aas_core_codegen.common import Error, assert_never
from aas_core_codegen.xsd import naming as xsd_naming

assert aas_core_codegen.xsd.__doc__ == __doc__


def _define_for_enumeration(enumeration: intermediate.Enumeration) -> List[ET.Element]:
    """
    Generate the definitions for an ``enumeration``.
    The root element is to be *extended* with the resulting list.
    """
    restriction = ET.Element("xs:restriction", {"base": "xs:string"})
    for literal in enumeration.literals:
        restriction.append(ET.Element("xs:enumeration", {"value": literal.value}))

    element = ET.Element(
        "xs:simpleType", {"name": xsd_naming.model_type(enumeration.name)}
    )
    element.append(restriction)

    return [element]


_PRIMITIVE_MAP = {
    intermediate.PrimitiveType.BOOL: "xs:boolean",
    intermediate.PrimitiveType.INT: "xs:long",
    intermediate.PrimitiveType.FLOAT: "xs:double",
    intermediate.PrimitiveType.STR: "xs:string",
    intermediate.PrimitiveType.BYTEARRAY: "xs:base64Binary",
}
assert all(literal in _PRIMITIVE_MAP for literal in intermediate.PrimitiveType)


def _generate_xs_restriction(
    base_type: intermediate.PrimitiveType,
    len_constraint: Optional[infer_for_schema.LenConstraint],
    pattern_constraints: Optional[Sequence[infer_for_schema.PatternConstraint]],
) -> Optional[ET.Element]:
    """
    Generate the ``xs:restriction`` for the given primitive.

    If there are no length and pattern constraints, return None.
    """
    if len_constraint is None and (
        pattern_constraints is None or (len(pattern_constraints) == 0)
    ):
        return None

    restriction = ET.Element("xs:restriction", {"base": _PRIMITIVE_MAP[base_type]})

    if pattern_constraints is not None and len(pattern_constraints) > 0:
        if len(pattern_constraints) == 1:
            pattern = ET.Element(
                "xs:pattern", {"value": pattern_constraints[0].pattern}
            )

            restriction.append(pattern)
        else:
            # BEFORE-RELEASE (mristin, 2021-12-13):
            #  test this and check that the XSD makes sense with somebody else!
            parent_restriction = restriction
            for pattern_constraint in pattern_constraints:
                nested_restriction = ET.Element("xs:restriction")
                pattern = ET.Element(
                    "xs:pattern", {"value": pattern_constraint.pattern}
                )

                nested_restriction.append(pattern)
                parent_restriction.append(nested_restriction)
                parent_restriction = nested_restriction

    if len_constraint is not None:
        if len_constraint.min_value is not None:
            min_length = ET.Element(
                "xs:minLength", {"value": str(len_constraint.min_value)}
            )
            restriction.append(min_length)

        if len_constraint.max_value is not None:
            max_length = ET.Element(
                "xs:maxLength", {"value": str(len_constraint.max_value)}
            )
            restriction.append(max_length)

    return restriction


def _generate_xs_element_for_a_primitive_property(
    prop: intermediate.Property,
    len_constraint: Optional[infer_for_schema.LenConstraint],
    pattern_constraints: Optional[Sequence[infer_for_schema.PatternConstraint]],
) -> ET.Element:
    """
    Generate the ``xs:element`` for a primitive property.

    A primitive property is a property whose type is either a primitive or
    a constrained primitive. The reason why we take these two together is that we
    in-line the constraints for the constrained primitives.

    We do not define the constrained primitives separately in the schema in order to
    avoid the confusion during comparisons between the XSD and the meta-model in
    the book.
    """
    type_anno = intermediate.beneath_optional(prop.type_annotation)

    # NOTE (mristin, 2022-03-30):
    # Specify the type of the ``type_anno`` here with assert instead of specifying it
    # in the pre-condition to help mypy a bit.
    assert isinstance(type_anno, intermediate.PrimitiveTypeAnnotation) or (
        isinstance(type_anno, intermediate.OurTypeAnnotation)
        and isinstance(type_anno.symbol, intermediate.ConstrainedPrimitive)
    ), f"Expected a primitive or a constrained primitive, but got: {type_anno}"

    base_type = None  # type: Optional[intermediate.PrimitiveType]
    if isinstance(type_anno, intermediate.PrimitiveTypeAnnotation):
        base_type = type_anno.a_type
    elif isinstance(type_anno, intermediate.OurTypeAnnotation) and isinstance(
        type_anno.symbol, intermediate.ConstrainedPrimitive
    ):
        base_type = type_anno.symbol.constrainee
    else:
        raise AssertionError(
            f"Unexpected type_anno type {type(type_anno)}: {type_anno}"
        )

    xs_restriction = _generate_xs_restriction(
        base_type=base_type,
        len_constraint=len_constraint,
        pattern_constraints=pattern_constraints,
    )

    xs_element = None  # type: Optional[ET.Element]
    if xs_restriction is None:
        xs_element = ET.Element(
            "xs:element",
            {
                "name": naming.xml_property(prop.name),
                "type": _PRIMITIVE_MAP[base_type],
            },
        )
    else:
        xs_simple_type = ET.Element("xs:simpleType")
        xs_simple_type.append(xs_restriction)

        xs_element = ET.Element("xs:element", {"name": naming.xml_property(prop.name)})
        xs_element.append(xs_simple_type)

    assert xs_element is not None
    return xs_element


@ensure(lambda result: (result[0] is not None) ^ (result[1] is not None))
def _generate_xs_element_for_a_list_property(
    prop: intermediate.Property,
    len_constraint: Optional[infer_for_schema.LenConstraint],
) -> Tuple[Optional[ET.Element], Optional[Error]]:
    """Generate the ``xs:element`` for a list property."""
    type_anno = intermediate.beneath_optional(prop.type_annotation)

    # NOTE (mristin, 2022-03-30):
    # Specify the ``type_anno`` here with assert instead of specifying it
    # in the pre-condition to help mypy a bit
    assert isinstance(type_anno, intermediate.ListTypeAnnotation)

    list_element = None  # type: Optional[ET.Element]

    min_occurs = "0"
    max_occurs = "unbounded"
    if len_constraint is not None:
        if len_constraint.min_value is not None:
            min_occurs = str(len_constraint.min_value)

        if len_constraint.max_value is not None:
            max_occurs = str(len_constraint.max_value)

    if isinstance(type_anno.items, intermediate.OurTypeAnnotation):
        # NOTE (mristin, 2021-11-13):
        # We need to nest the elements in the tag element to separate them in the
        # sequence.

        if isinstance(
            type_anno.items.symbol,
            (
                intermediate.Enumeration,
                intermediate.AbstractClass,
                intermediate.ConcreteClass,
            ),
        ):
            list_element = ET.Element("xs:sequence")
            list_element.append(
                ET.Element(
                    "xs:element",
                    {
                        "minOccurs": min_occurs,
                        "maxOccurs": max_occurs,
                        "name": naming.xml_class_name(type_anno.items.symbol.name),
                        "type": xsd_naming.model_type(type_anno.items.symbol.name),
                    },
                )
            )
        elif isinstance(type_anno.items.symbol, intermediate.ConstrainedPrimitive):
            return None, Error(
                prop.parsed.node,
                f"We do not know how to specify the list of constrained primitives "
                f"for the property {prop.name!r} of {prop.specified_for.name!r} "
                f"in the XSD with the type: {type_anno}",
            )
        else:
            assert_never(type_anno.items.symbol)

    else:
        return None, Error(
            prop.parsed.node,
            f"We do not know how to specify the list "
            f"for the property {prop.name!r} of {prop.specified_for.name!r} "
            f"in the XSD with the type: {type_anno}",
        )

    assert list_element is not None

    xs_complex_type = ET.Element("xs:complexType")
    xs_complex_type.append(list_element)

    xs_element = ET.Element("xs:element", {"name": naming.xml_property(prop.name)})
    xs_element.append(xs_complex_type)

    return xs_element, None


@ensure(lambda result: (result[0] is not None) ^ (result[1] is not None))
def _generate_xs_element_for_a_property(
    prop: intermediate.Property,
    len_constraint: Optional[infer_for_schema.LenConstraint],
    pattern_constraints: Optional[Sequence[infer_for_schema.PatternConstraint]],
) -> Tuple[Optional[ET.Element], Optional[Error]]:
    """Generate the definition of an ``xs:element`` for a property."""
    type_anno = intermediate.beneath_optional(prop.type_annotation)

    xs_element = None  # type: Optional[ET.Element]

    if isinstance(type_anno, intermediate.PrimitiveTypeAnnotation):
        xs_element = _generate_xs_element_for_a_primitive_property(
            prop=prop,
            len_constraint=len_constraint,
            pattern_constraints=pattern_constraints,
        )

    elif isinstance(type_anno, intermediate.OurTypeAnnotation):
        if isinstance(
            type_anno.symbol,
            (
                intermediate.Enumeration,
                intermediate.AbstractClass,
                intermediate.ConcreteClass,
            ),
        ):
            xs_element = ET.Element(
                "xs:element",
                {
                    "name": naming.xml_property(prop.name),
                    "type": xsd_naming.model_type(type_anno.symbol.name),
                },
            )

        elif isinstance(type_anno.symbol, intermediate.ConstrainedPrimitive):
            xs_element = _generate_xs_element_for_a_primitive_property(
                prop=prop,
                len_constraint=len_constraint,
                pattern_constraints=pattern_constraints,
            )

        else:
            assert_never(type_anno.symbol)

    elif isinstance(type_anno, intermediate.ListTypeAnnotation):
        xs_element, error = _generate_xs_element_for_a_list_property(
            prop=prop, len_constraint=len_constraint
        )
        if error is not None:
            return None, error

        assert xs_element is not None
    else:
        assert_never(type_anno)

    assert xs_element is not None

    if isinstance(prop.type_annotation, intermediate.OptionalTypeAnnotation):
        xs_element.attrib["minOccurs"] = "0"

    return xs_element, None


@ensure(lambda result: (result[0] is not None) ^ (result[1] is not None))
def _define_properties(
    cls: intermediate.ClassUnion,
    constraints_by_property: infer_for_schema.ConstraintsByProperty,
) -> Tuple[Optional[List[ET.Element]], Optional[List[Error]]]:
    """Define the properties of the ``cls`` as a sequence of tags."""
    sequence = []  # type: List[ET.Element]
    errors = []  # type: List[Error]

    for prop in cls.properties:
        if prop.specified_for is not cls:
            continue

        len_constraint = constraints_by_property.len_constraints_by_property.get(
            prop, None
        )

        pattern_constraints = constraints_by_property.patterns_by_property.get(
            prop, None
        )

        xs_element, error = _generate_xs_element_for_a_property(
            prop=prop,
            len_constraint=len_constraint,
            pattern_constraints=pattern_constraints,
        )
        if error is not None:
            errors.append(error)
        else:
            assert xs_element is not None
            sequence.append(xs_element)

    if len(errors) > 0:
        return None, errors

    return sequence, None


def _generate_xs_group_for_class(
    cls: intermediate.ClassUnion,
    constraints_by_property: infer_for_schema.ConstraintsByProperty,
) -> Tuple[Optional[ET.Element], Optional[Error]]:
    """Generate the ``xs:group`` representation of the class properties."""
    properties, properties_errors = _define_properties(
        cls=cls, constraints_by_property=constraints_by_property
    )

    if properties_errors is not None:
        return None, Error(
            cls.parsed.node,
            f"Failed to generate xs:group for the class {cls.name!r}",
            properties_errors,
        )

    assert properties is not None

    xs_sequence = ET.Element("xs:sequence")
    for inheritance in cls.inheritances:
        inheritance_xs_group = ET.Element(
            "xs:group", {"ref": xsd_naming.group_name(inheritance.name)}
        )
        xs_sequence.append(inheritance_xs_group)

    xs_sequence.extend(properties)

    xs_group = ET.Element("xs:group", {"name": xsd_naming.group_name(cls.name)})
    xs_group.append(xs_sequence)

    return xs_group, None


@ensure(lambda result: (result[0] is not None) ^ (result[1] is not None))
def _define_for_class(
    cls: intermediate.ClassUnion,
    constraints_by_property: infer_for_schema.ConstraintsByProperty,
) -> Tuple[Optional[List[ET.Element]], Optional[Error]]:
    """
    Generate the definitions for the class ``cls``.

    The root element is to be *extended* with the resulting list.
    """
    # NOTE (mristin, 2022-03-30):
    # We define each set of properties in a group. Then we reference these groups
    # among the complex types.
    # See: https://stackoverflow.com/questions/1198755/xml-schemas-with-multiple-inheritance

    xs_group, xs_group_error = _generate_xs_group_for_class(
        cls=cls, constraints_by_property=constraints_by_property
    )
    if xs_group_error is not None:
        return None, xs_group_error

    assert xs_group is not None

    xs_group_ref = ET.Element("xs:group", {"ref": xsd_naming.group_name(cls.name)})

    xs_sequence = ET.Element("xs:sequence")
    xs_sequence.append(xs_group_ref)

    complex_type = ET.Element(
        "xs:complexType", {"name": xsd_naming.model_type(cls.name)}
    )
    complex_type.append(xs_sequence)

    return [xs_group, complex_type], None


_WHITESPACE_RE = re.compile(r"\s+")


@ensure(lambda result: (result[0] is not None) ^ (result[1] is not None))
def _retrieve_implementation_specific_elements(
    cls: intermediate.ClassUnion,
    spec_impls: specific_implementations.SpecificImplementations,
) -> Tuple[Optional[List[ET.Element]], Optional[List[Error]]]:
    """Parse the elements from the implementation-specific snippet."""
    implementation_key = specific_implementations.ImplementationKey(f"{cls.name}.xml")

    text = spec_impls.get(implementation_key, None)
    if text is None:
        return None, [
            Error(
                cls.parsed.node,
                f"The implementation is missing "
                f"for the implementation-specific class: {implementation_key}",
            )
        ]

    # noinspection PyUnusedLocal
    implementation_root = None  # type: Optional[ET.Element]

    try:
        implementation_root = ET.fromstring(text)
    except Exception as err:
        return None, [
            Error(
                cls.parsed.node,
                f"Failed to parse the XML out of "
                f"the specific implementation {implementation_key}: {err}",
            )
        ]

    assert implementation_root is not None

    errors = []  # type: List[Error]
    for descendant in implementation_root.iter():
        if descendant.text is not None and not _WHITESPACE_RE.fullmatch(
            descendant.text
        ):
            errors.append(
                Error(
                    cls.parsed.node,
                    f"Unexpected text "
                    f"in the specific implementation {implementation_key} "
                    f"in a tag {descendant.tag!r}: {descendant.text!r}",
                )
            )
            continue

        if descendant.tail is not None and not _WHITESPACE_RE.fullmatch(
            descendant.tail
        ):
            errors.append(
                Error(
                    cls.parsed.node,
                    f"Unexpected tail text "
                    f"in the specific implementation {implementation_key} "
                    f"in a tag {descendant.tag!r}: {descendant.tail!r}",
                )
            )
            continue

    if len(errors) > 0:
        return None, errors

    # Ignore the implementation root since it defines a partial schema
    elements = []  # type: List[ET.Element]
    for child in implementation_root:
        elements.append(child)

    return elements, None


@ensure(lambda result: (result[0] is not None) ^ (result[1] is not None))
def _generate(
    symbol_table: intermediate.SymbolTable,
    spec_impls: specific_implementations.SpecificImplementations,
) -> Tuple[Optional[str], Optional[List[Error]]]:
    """Generate the XML Schema Definition (XSD) based on the ``symbol_table."""
    root_element_key = specific_implementations.ImplementationKey("root_element.xml")

    root_element_as_text = spec_impls.get(root_element_key, None)
    if root_element_as_text is None:
        return None, [
            Error(
                None,
                f"The implementation snippet for the root element "
                f"is missing: {root_element_key}",
            )
        ]

    # noinspection PyUnusedLocal
    root = None  # type: Optional[ET.Element]
    try:
        root = ET.fromstring(root_element_as_text)
    except ET.ParseError as err:
        return None, [
            Error(
                None, f"Failed to parse the root element from {root_element_key}: {err}"
            )
        ]

    assert root is not None

    errors = []  # type: List[Error]

    constraints_by_class, some_errors = infer_for_schema.infer_constraints_by_class(
        symbol_table=symbol_table
    )

    if some_errors is not None:
        errors.extend(some_errors)

    if len(errors) > 0:
        return None, errors

    assert constraints_by_class is not None

    ids_of_symbols_in_properties = intermediate.collect_ids_of_symbols_in_properties(
        symbol_table=symbol_table
    )

    for symbol in symbol_table.symbols:
        elements = None  # type: Optional[List[ET.Element]]

        if (
            isinstance(symbol, (intermediate.AbstractClass, intermediate.ConcreteClass))
            and symbol.is_implementation_specific
        ):
            elements, impl_spec_errors = _retrieve_implementation_specific_elements(
                cls=symbol, spec_impls=spec_impls
            )
            if impl_spec_errors is not None:
                errors.extend(impl_spec_errors)
                continue

            assert elements is not None
        else:
            if isinstance(symbol, intermediate.Enumeration):
                if id(symbol) not in ids_of_symbols_in_properties:
                    continue

                elements = _define_for_enumeration(enumeration=symbol)

            elif isinstance(symbol, intermediate.ConstrainedPrimitive):
                # NOTE (mristin, 2022-03-30):
                # We in-line the constraints from the constrained primitives directly
                # in the properties. We do not want to introduce separate definitions
                # for them as that would make it more difficult for downstream code
                # generators to generate meaningful code.

                continue

            elif isinstance(
                symbol, (intermediate.AbstractClass, intermediate.ConcreteClass)
            ):
                elements, definition_error = _define_for_class(
                    cls=symbol, constraints_by_property=constraints_by_class[symbol]
                )

                if definition_error is not None:
                    errors.append(definition_error)
                    continue
            else:
                assert_never(symbol)

        assert elements is not None
        root.extend(elements)

    if len(errors) > 0:
        return None, errors

    observed_definitions = dict()  # type: MutableMapping[str, ET.Element]
    for element in root:
        name = element.attrib.get("name", None)
        if name is None:
            continue

        observed = observed_definitions.get(name, None)
        if observed is not None:
            ours = ET.tostring(element, encoding="unicode", method="xml")
            theirs = ET.tostring(observed, encoding="unicode", method="xml")

            errors.append(
                Error(
                    None,
                    f"There are conflicting definitions in the schema "
                    f"with the name {name!r}:\n"
                    f"\n"
                    f"{ours}\n"
                    f"\n"
                    f"and\n"
                    f"\n"
                    f"{theirs}",
                )
            )
        else:
            observed_definitions[name] = element

    if len(errors) > 0:
        return None, errors

    text = ET.tostring(root, encoding="unicode", method="xml")

    # NOTE (mristin, 2021-11-23):
    # This approach is slow, but effective. As long as the meta-model is not too big,
    # this should work.
    # noinspection PyUnresolvedReferences
    pretty_text = xml.dom.minidom.parseString(text).toprettyxml(indent="  ")

    return pretty_text, None


def execute(context: run.Context, stdout: TextIO, stderr: TextIO) -> int:
    """Generate the code."""
    code, errors = _generate(
        symbol_table=context.symbol_table, spec_impls=context.spec_impls
    )

    if errors is not None:
        run.write_error_report(
            message=f"Failed to generate the XML Schema Definition "
            f"based on {context.model_path}",
            errors=[context.lineno_columner.error_message(error) for error in errors],
            stderr=stderr,
        )
        return 1

    assert code is not None

    pth = context.output_dir / "schema.xml"
    try:
        pth.write_text(code)
    except Exception as exception:
        run.write_error_report(
            message=f"Failed to write the XML Schema Definition to {pth}",
            errors=[str(exception)],
            stderr=stderr,
        )
        return 1

    stdout.write(f"Code generated to: {context.output_dir}\n")
    return 0