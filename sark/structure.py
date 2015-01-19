from . import exceptions
import idaapi
from . import sark
import idautils
import idc
from collections import namedtuple, defaultdict
import operator


FF_TYPES = [idc.FF_BYTE, idc.FF_WORD, idc.FF_DWRD, idc.FF_QWRD, idc.FF_OWRD, ]
FF_SIZES = [1, 2, 4, 8, 16, ]

SIZE_TO_TYPE = dict(zip(FF_SIZES, FF_TYPES))

DTYP_TO_SIZE = {
    idaapi.dt_byte: 1,
    idaapi.dt_word: 2,
    idaapi.dt_dword: 4,
    idaapi.dt_float: 4,
    idaapi.dt_double: 8,
    idaapi.dt_qword: 8,
    idaapi.dt_byte16: 16,
    idaapi.dt_fword: 6,
    idaapi.dt_3byte: 3,
    idaapi.dt_byte32: 32,
    idaapi.dt_byte64: 64,
}

STRUCT_ERROR_MAP = {
    idc.STRUC_ERROR_MEMBER_NAME:
        (exceptions.SarkErrorStructMemberName, "already has member with this name (bad name)"),
    idc.STRUC_ERROR_MEMBER_OFFSET:
        (exceptions.SarkErrorStructMemberOffset, "already has member at this offset"),
    idc.STRUC_ERROR_MEMBER_SIZE:
        (exceptions.SarkErrorStructMemberSize, "bad number of bytes or bad sizeof(type)"),
    idc.STRUC_ERROR_MEMBER_TINFO:
        (exceptions.SarkErrorStructMemberTinfo, "bad typeid parameter"),
    idc.STRUC_ERROR_MEMBER_STRUCT:
        (exceptions.SarkErrorStructMemberStruct, "bad struct id (the 1st argument)"),
    idc.STRUC_ERROR_MEMBER_UNIVAR:
        (exceptions.SarkErrorStructMemberUnivar, "unions can't have variable sized members"),
    idc.STRUC_ERROR_MEMBER_VARLAST:
        (exceptions.SarkErrorStructMemberVarlast, "variable sized member should be the last member in the structure"),
}


def struct_member_error(err, sid, name, offset, size):
    exception, msg = STRUCT_ERROR_MAP[err]
    struct_name = idc.GetStrucName(sid)
    return exception(('AddStructMember(struct="{}", member="{}", offset={}, size={}) '
                      'failed: {}').format(
        struct_name,
        name,
        offset,
        size,
        msg
    ))


def create_struct(name):
    sid = idc.GetStrucIdByName(name)
    if sid != 0xFFFFFFFF:
        # The struct already exists.
        raise exceptions.SarkStructAlreadyExists()

    sid = idc.AddStrucEx(-1, name, 0)
    if sid == 0xFFFFFFFF:
        raise exceptions.SarkStructCreationFailed()

    return sid


def get_struct(name):
    sid = idc.GetStrucIdByName(name)
    if sid == 0xFFFFFFFF:
        raise exceptions.SarkStructNotFound()

    return sid


def size_to_flags(size):
    return SIZE_TO_TYPE[size] | idc.FF_DATA


def add_struct_member(sid, name, offset, size):
    idaapi.msg("{}, {}\n".format(offset, size))
    failure = idc.AddStrucMember(sid, name, offset, size_to_flags(size), -1, size)

    if failure:
        raise struct_member_error(failure, sid, name, offset, size)


StructOffset = namedtuple("StructOffset", "offset size")
OperandRef = namedtuple("OperandRef", "ea n")


def dtyp_to_size(dtyp):
    return DTYP_TO_SIZE[dtyp]


def infer_struct_offsets(start, end, reg_name):
    offsets = set()
    operands = []
    for line in sark.iter_lines(start, end):
        inst = line.inst
        if not sark.is_reg_in_inst(inst, reg_name):
            continue

        for operand in inst.Operands:
            if not sark.is_reg_in_operand(operand, reg_name):
                continue

            if not sark.operand_has_displacement(operand):
                continue

            offset = sark.operand_get_displacement(operand)
            size = dtyp_to_size(operand.dtyp)
            offsets.add(StructOffset(offset, size))
            operands.append(OperandRef(line.ea, operand.n))

    return offsets, operands


def get_common_register(start, end):
    registers = defaultdict(int)
    for line in sark.iter_lines(start, end):
        inst = line.inst

        for operand in inst.Operands:

            if not sark.operand_has_displacement(operand):
                continue

            # TODO: use the operand dtyp size here to get the proper register name.
            register_name = sark.get_register_name(operand.reg)
            registers[register_name] += 1

    return max(registers.iteritems(), key=operator.itemgetter(1))[0]


def offset_name(offset):
    return "offset_{:X}".format(offset.offset)


def set_struct_offsets(offsets, sid):
    for offset in offsets:
        try:
            add_struct_member(sid,
                              offset_name(offset),
                              offset.offset,
                              offset.size)
        except exceptions.SarkErrorStructMemberName:
            # Get the offset of the member with the same name
            existing_offset = idc.GetMemberOffset(sid, offset_name(offset))
            if offset.offset == existing_offset:
                pass
            else:
                raise


def create_struct_from_offsets(name, offsets):
    sid = create_struct(name)

    set_struct_offsets(offsets, sid)


def apply_struct(start, end, reg_name, struct_name):
    offsets, operands = infer_struct_offsets(start, end, reg_name)

    sid = get_struct(struct_name)

    for ea, n in operands:
        idc.OpStroff(ea, n, sid)