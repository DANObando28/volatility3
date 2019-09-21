# This file is Copyright 2019 Volatility Foundation and licensed under the Volatility Software License 1.0
# which is available at https://www.volatilityfoundation.org/license/vsl_v1.0
#
import binascii
import code
import struct
import sys
from typing import Any, Dict, List, Optional

from volatility.framework import renderers, interfaces
from volatility.framework.configuration import requirements
from volatility.framework.layers import intel

try:
    import capstone

    has_capstone = True
except ImportError:
    has_capstone = False


class Volshell(interfaces.plugins.PluginInterface):
    """Shell environment to directly interact with a memory image."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__current_layer = None  # type: Optional[str]

    @classmethod
    def get_requirements(cls) -> List[interfaces.configuration.RequirementInterface]:
        return [
            requirements.TranslationLayerRequirement(name = 'primary',
                                                     description = 'Memory layer for the kernel',
                                                     architectures = ["Intel32", "Intel64"])
        ]

    def run(self, additional_locals: Dict[str, Any] = None) -> interfaces.renderers.TreeGrid:
        """Runs the interactive volshell plugin.

        Returns:
            Return a TreeGrid but this is always empty since the point of this plugin is to run interactively
        """

        self._current_layer = self.config['primary']

        # Try to enable tab completion
        try:
            import readline
        except ImportError:
            pass
        else:
            import rlcompleter
            completer = rlcompleter.Completer(namespace = self.construct_locals())
            readline.set_completer(completer.complete)
            readline.parse_and_bind("tab: complete")
            print("Readline imported successfully")

        # TODO: provide help, consider generic functions (pslist?) and/or providing windows/linux functions

        sys.ps1 = "({}) >>> ".format(self.current_layer)
        code.interact(banner = "\nCall help() to see available functions\n", local = self.construct_locals())

        return renderers.TreeGrid([("Terminating", str)], None)

    def help(self):
        """Describes the available commands"""
        variables = []
        print("\nMethods:")
        for name, item in self.construct_locals().items():
            if item.__doc__ and callable(item):
                print("* {}".format(name))
                print("    {}".format(item.__doc__))
            else:
                variables.append(name)

        print("\nVariables:")
        for var in variables:
            print("  {}".format(var))

    def construct_locals(self) -> Dict[str, Any]:
        """Returns a dictionary listing the functions to be added to the
        environment."""
        return {
            'dt': self.display_type,
            'display_type': self.display_type,
            'db': self.display_bytes,
            'display_bytes': self.display_bytes,
            'dw': self.display_words,
            'display_words': self.display_words,
            'dd': self.display_doublewords,
            'display_doublewords': self.display_doublewords,
            'dq': self.display_quadwords,
            'display_quadwords': self.display_quadwords,
            'dis': self.disassemble,
            'disassemble': self.disassemble,
            'cl': self.change_layer,
            'change_layer': self.change_layer,
            'context': self.context,
            'self': self,
            'hh': self.help,
            'help': self.help,
        }

    def _read_data(self, offset, count = 128, layer_name = None):
        """Reads the bytes necessary for the display_* methods"""
        return self.context.layers[layer_name or self.current_layer].read(offset, count)

    def _display_data(self, offset: int, remaining_data: bytes, format_string: str = "B", ascii: bool = True):
        """Display a series of bytes"""
        chunk_size = struct.calcsize(format_string)
        data_length = len(remaining_data)
        remaining_data = remaining_data[:data_length - (data_length % chunk_size)]

        while remaining_data:
            current_line, remaining_data = remaining_data[:16], remaining_data[16:]
            offset += 16

            data_blocks = [current_line[chunk_size * i:chunk_size * (i + 1)] for i in range(16 // chunk_size)]
            data_blocks = [x for x in data_blocks if x != b'']
            valid_data = [("{:0" + str(2 * chunk_size) + "x}").format(struct.unpack(format_string, x)[0])
                          for x in data_blocks]
            padding_data = [" " * 2 * chunk_size for _ in range((16 - len(current_line)) // chunk_size)]
            hex_data = " ".join(valid_data + padding_data)

            ascii_data = ""
            if ascii:
                connector = " "
                if chunk_size < 2:
                    connector = ""
                ascii_data = connector.join([self._ascii_bytes(x) for x in valid_data])

            print(hex(offset), "  ", hex_data, "  ", ascii_data)

    @staticmethod
    def _ascii_bytes(bytes):
        """Converts bytes into an ascii string"""
        return "".join([chr(x) if 32 < x < 127 else '.' for x in binascii.unhexlify(bytes)])

    @property
    def current_layer(self):
        return self._current_layer

    def change_layer(self, layer_name = None):
        """Changes the current default layer"""
        if not layer_name:
            layer_name = self.config['primary']
        self._current_layer = layer_name
        sys.ps1 = "({}) >>> ".format(self.current_layer)

    def display_bytes(self, offset, count = 128, layer_name = None):
        """Displays byte values and ASCII characters"""
        remaining_data = self._read_data(offset, count = count, layer_name = layer_name)
        self._display_data(offset, remaining_data)

    def display_quadwords(self, offset, count = 128, layer_name = None):
        """Displays quad-word values (8 bytes) and corresponding ASCII characters"""
        remaining_data = self._read_data(offset, count = count, layer_name = layer_name)
        self._display_data(offset, remaining_data, format_string = "Q")

    def display_doublewords(self, offset, count = 128, layer_name = None):
        """Displays double-word values (4 bytes) and corresponding ASCII characters"""
        remaining_data = self._read_data(offset, count = count, layer_name = layer_name)
        self._display_data(offset, remaining_data, format_string = "I")

    def display_words(self, offset, count = 128, layer_name = None):
        """Displays word values (2 bytes) and corresponding ASCII characters"""
        remaining_data = self._read_data(offset, count = count, layer_name = layer_name)
        self._display_data(offset, remaining_data, format_string = "H")

    def disassemble(self, offset, count = 128, layer_name = None, architecture = None):
        """Disassembles a number of instructions from the code at offset"""
        remaining_data = self._read_data(offset, count = count, layer_name = layer_name)
        if not has_capstone:
            print("Capstone not available - please install it to use the disassemble command")
        else:
            if isinstance(self.context.layers[layer_name or self.current_layer], intel.Intel32e):
                architecture = 'intel64'
            elif isinstance(self.context.layers[layer_name or self.current_layer], intel.Intel):
                architecture = 'intel'
            disasm_types = {
                'intel': capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32),
                'intel64': capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64),
                'arm': capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM),
                'arm64': capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
            }
            if architecture is not None:
                for i in disasm_types[architecture].disasm(remaining_data, offset):
                    print("0x%x:\t%s\t%s" % (i.address, i.mnemonic, i.op_str))

    @staticmethod
    def display_type(object: interfaces.objects.ObjectInterface):
        """Display Type describes the members of a particular object in alphabetical order"""
        longest_member = longest_offset = 0
        for member in object.vol.members:
            relative_offset, member_type = object.vol.members[member]
            longest_member = max(len(member), longest_member)
            longest_offset = max(len(hex(relative_offset)), longest_offset)

        for member in object.vol.members:
            relative_offset, member_type = object.vol.members[member]
            len_offset = len(hex(relative_offset))
            len_member = len(member)
            print(" " * (longest_offset - len_offset), hex(relative_offset), "\t\t", member,
                  " " * (longest_member - len_member), "\t\t", member_type.vol.type_name)
