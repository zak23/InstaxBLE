#!/usr/bin/env python3

from math import ceil
from struct import pack, unpack_from
from time import sleep

# Try to import Types with a relative import first
try:
    from .Types import EventType, InfoType, PrinterStatus, DeviceInfo
    from . import LedPatterns
except ImportError:
    # If that fails (which it will if this file is being run directly),
    # try an absolute import instead
    from Types import EventType, InfoType, PrinterStatus, DeviceInfo
    import LedPatterns

import argparse

import simplepyble
import sys
from PIL import Image
from io import BytesIO
from typing import List, Union


class PrinterInfo:
    def __init__(self, isDummy: bool = False) -> None:
        """
        Initializes printer info with default or dummy values.
        - isDummy: if true, populates the printer info with dummy values for testing without a real printer
        """
        self.status: PrinterStatus = (
            PrinterStatus.UNKNOWN if not isDummy else PrinterStatus.OK
        )
        self.isDummy: bool = isDummy
        self.printEnabled: bool = False
        self.modelName: str = ""
        self.modelNumber: str = ""
        self.width: int = 0 if not isDummy else 600
        self.height: int = 0 if not isDummy else 800
        self.chunkSize: int = 0 if not isDummy else 900
        self.maxFileSizeKb: int = 0 if not isDummy else 105
        self.photosLeft: int = 0 if not isDummy else 10
        self.isCharging: bool = False
        self.accelerometer = (0, 0, 0, 0)  # used for accelerometer data
        self.batteryState = 0
        self.batteryPercentage = 0
        # Next two values are not used by InstaxBLE but are part of the printer info
        self.supportedPictureTypes: int = 0
        self.supportedPictureOptions: int = 0
        self.peripheral = None

    @property
    def imageSize(self) -> tuple:
        return (self.width, self.height)

    def print_values(self) -> None:
        print("\nPrinter Info:")
        print(f"  Print Enabled:      {self.printEnabled}")
        print(f"  Model Name:         {self.modelName}")
        print(f"  Model Number:       {self.modelNumber}")
        print(f"  Width:              {self.width}")
        print(f"  Height:             {self.height}")
        print(f"  Chunk Size:         {self.chunkSize}")
        print(f"  Max File Size (KB): {self.maxFileSizeKb}")
        print(f"  Photos Left:        {self.photosLeft}")
        print(f"  Is Charging:        {self.isCharging}")
        print(f"  Battery State:      {self.batteryState}")
        print(f"  Battery Percentage: {self.batteryPercentage}%")
        # print(f"  Supported Picture Types: {self.supportedPictureTypes}")
        # print(f"  Supported Picture Options: {self.supportedPictureOptions}")


class InstaxBLE:
    def __init__(
        self,
        printer_address=None,
        printer_name=None,
        print_enabled=False,
        dummy_printer=False,
        verbose=False,
        quiet=False,
        image_path=None,
    ):

        self.waiting_for_response = False
        self.printerInfo = PrinterInfo(dummy_printer)

        # Main printer service and characteristics UUIDs
        self.serviceUUID = "70954782-2d83-473d-9e5f-81e1d02d5273"
        self.writeCharUUID = "70954783-2d83-473d-9e5f-81e1d02d5273"
        self.notifyCharUUID = "70954784-2d83-473d-9e5f-81e1d02d5273"

        # Generic Bluetooth Dvice Information Service
        self.deviceInformationServiceUUID = "0000180a-0000-1000-8000-00805f9b34fb"
        self.modelNameStringUUID = "00002a24-0000-1000-8000-00805f9b34fb"

        self.peripheral = None

        self.quiet = quiet  # suppress non-error output
        self.verbose = verbose  # enable verbose/debug output
        self.printEnabled = print_enabled
        self.printerName = printer_name.upper() if printer_name else None
        self.printerAddress = printer_address.upper() if printer_address else None
        self.image_path = image_path
        self.packetsForPrinting = []
        self.printCancelled = False

        adapters = simplepyble.Adapter.get_adapters()
        if len(adapters) == 0:
            if not self.quiet:
                sys.exit("No bluetooth adapters found (are they enabled?)")
            else:
                sys.exit()

        if len(adapters) > 1:
            self.logVerbose(f"Found multiple adapters: {', '.join([adapter.identifier() for adapter in adapters])}")
            self.logVerbose(f"Using the first one: {adapters[0].identifier()}")
        self.adapter = adapters[0]

    def log(self, msg: str) -> None:
        """Print a message, unless in quiet mode"""
        if self.quiet:
            return
        print(msg)

    def logVerbose(self, msg: str) -> None:
        """Print a verbose message if verbose mode is enabled unless in quiet mode"""
        if self.verbose and not self.quiet:
            print(msg)

    def parse_printer_response(self, packet: bytes) -> None:
        """Parse the response packet from the printer"""
        header, length, op1, op2, printer_status = unpack_from(">HHBBB", packet[:7])
        self.printerInfo.status = printer_status
        self.logVerbose(
            f"\tprinter status: {printer_status}, {PrinterStatus(printer_status) if printer_status in PrinterStatus._value2member_map_ else 'Unknown (not in list)'}"
        )

        try:
            event = EventType((op1, op2))
            self.logVerbose(f"\tResponse event: {event}")
        except ValueError:
            self.logVerbose(f"Unknown EventType: ({op1}, {op2})")
            return

        self.logVerbose(f"event: {event}")

        if event == EventType.XYZ_AXIS_INFO:
            x, y, z, o = unpack_from("<hhhB", packet[6:-1])
            self.accelerometer = (x, y, z, o)
        elif event == EventType.LED_PATTERN_SETTINGS:
            pass
        elif event == EventType.SUPPORT_FUNCTION_INFO:
            try:
                infoType = InfoType(packet[7])
            except ValueError:
                self.logVerbose(f"Unknown InfoType: {packet[7]}")
                return

            if infoType == InfoType.IMAGE_SUPPORT_INFO:
                (
                    width,
                    height,
                    support_pic_type,
                    support_pic_option,
                    support_jpeg_size,
                ) = unpack_from(">HHBBI", packet[8:])

                self.printerInfo.width = width
                self.printerInfo.height = height
                self.printerInfo.supportedPictureTypes = support_pic_type
                self.printerInfo.supportedPictureOptions = support_pic_option
                self.printerInfo.maxFileSizeKb = support_jpeg_size // 1024

            elif infoType == InfoType.BATTERY_INFO:
                self.printerInfo.batteryState, self.printerInfo.batteryPercentage = (
                    unpack_from(">BB", packet[8:10])
                )

            elif infoType == InfoType.PRINTER_FUNCTION_INFO:
                dataByte = packet[8]
                self.printerInfo.photosLeft = dataByte & 15
                self.printerInfo.isCharging = (1 << 7) & dataByte >= 1
                # self.log(f'photos left: {self.printerInfo.photosLeft}')
                # if self.printerInfo.isCharging:
                #     self.logVerbose('Printer is charging')
                # else:
                #     self.logVerbose('Printer is running on battery')

        elif event == EventType.PRINT_IMAGE_DOWNLOAD_START:
            if (len(packet) - 8) > 0:
                self.printerInfo.chunkSize = unpack_from(">H", packet[9:11])[0]
                self.logVerbose(f"Set chunk size to: {self.printerInfo.chunkSize}")
            else:
                self.log("Error: no chunk size found! Can't print")
                # print(packet[9:])
                self.cancel_print()
            # self.handle_image_packet_queue()

        elif event == EventType.PRINT_IMAGE_DOWNLOAD_DATA:
            pass

        elif event == EventType.PRINT_IMAGE_DOWNLOAD_END:
            pass

        elif event == EventType.PRINT_IMAGE_DOWNLOAD_CANCEL:
            pass

        elif event == EventType.PRINT_IMAGE:
            pass

        elif event == EventType.DEVICE_INFO_SERVICE:
            try:
                infoType = DeviceInfo(packet[7])
            except ValueError:
                self.logVerbose(f"Unknown DeviceInfo: {packet[7]}")
                return

            payload = packet[8:-1]  # remove header and checksum

            if infoType == DeviceInfo.MANUFACTURER_NAME:
                manufacturer_name = payload.decode("utf-8")
            elif infoType == DeviceInfo.MODEL_NUMBER:
                self.printerInfo.modelNumber = model_number = payload.decode("utf-8")
            elif infoType == DeviceInfo.SERIAL_NUMBER:
                serial_number = payload.decode("utf-8")
            elif infoType == DeviceInfo.HW_REVISION:
                hardware_revision = payload.decode("utf-8")
            elif infoType == DeviceInfo.SW_REVISION:
                software_revision = payload.decode("utf-8")
            elif infoType == DeviceInfo.FW_REVISION:
                firmware_revision = payload.decode("utf-8")
            elif infoType == DeviceInfo.SYSTEM_ID:
                system_id = payload.hex()
            elif infoType == DeviceInfo.REGULATORY_DATA:
                regulatory_data = payload.hex()
            elif infoType == DeviceInfo.PNP_ID:
                pnp_id = payload.hex()
            elif infoType == DeviceInfo.BT_FW_VERSION:
                bt_fw_version = payload.decode("utf-8")
            elif infoType == DeviceInfo.BT_OTA_FW_VERSION:
                bt_ota_fw_version = payload.decode("utf-8")
            else:
                self.logVerbose(f"Unhandled DeviceInfo type: {infoType}")
        # elif event == EventType.REJECT_FILM_COVER:
        #     print(packet[7:])
        #     print(self.prettify_bytearray(packet[7:]))
        #     self.logVerbose('Film cover rejected')
        else:
            self.logVerbose(f"Uncaught response from printer. Eventype: {event}")

    def notification_handler(self, packet: bytes) -> None:
        """Gets called whenever the printer replies and handles parsing the received data"""
        self.logVerbose(f"@notification_handler incoming packet: {packet[:40]}")
        self.logVerbose(f"\t{self.prettify_bytearray(packet[:40])}")
        if len(packet) < 8:
            self.log(f"Error: response packet size should be >= 8 (was {len(packet)})!")
            return
        if not self.validate_checksum(packet):
            self.log("Response packet checksum was invalid!")
            return

        self.parse_printer_response(packet)
        self.waiting_for_response = False

    def connect(self, timeout: int = 0) -> None:
        """Connect to the printer. Stops trying after <timeout> seconds if <timeout> is not zero."""
        if self.printerInfo.isDummy:
            return

        self.peripheral = self.scan_for_device(timeout=timeout)
        if not self.peripheral:
            return

        try:
            self.log(f"Connecting to {self.peripheral.identifier()} [{self.peripheral.address()}]")
            self.peripheral.connect()
        except Exception as e:
            if not self.quiet:
                self.log(f"error on connecting: {e}")

        if not self.peripheral.is_connected():
            return

        self.log("Printer connected")
        try:  # Attach notification handler
            self.peripheral.notify(
                self.serviceUUID, self.notifyCharUUID, self.notification_handler
            )
        except Exception as e:
            if not self.quiet:
                self.log(f"Error on attaching notification_handler: {e}")
                return

        self.get_printer_info()
        if self.verbose:
            self.printerInfo.print_values()

    def disconnect(self) -> None:
        """Disconnect from the printer"""
        self.waiting_for_response = False

        if self.printerInfo.isDummy or not self.peripheral:
            return

        if self.peripheral.is_connected():
            self.log("Disconnecting from printer...")
            self.peripheral.disconnect()

    def cancel_print(self) -> None:
        self.packetsForPrinting = []
        self.waiting_for_response = False
        self.send_packet(self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_CANCEL))

    def enable_printing(self) -> None:
        """Enable printing: send all image data and start the print"""
        self.printEnabled = True

    def disable_printing(self) -> None:
        """Disable printing: send all image data but do NOT start the actual print"""
        self.printEnabled = False

    def scan_for_device(self, timeout: int = 0) -> None:
        """Scan for our device and return it when found"""
        self.log("Searching for Instax printer...")
        secondsTried = 0
        scan_duration = 2  # seconds
        try:
            while True:
                self.adapter.scan_for(scan_duration * 1000)
                peripherals = self.adapter.scan_get_results()
                for peripheral in peripherals:
                    foundName = peripheral.identifier()
                    foundAddress = peripheral.address()
                    # 1. Match by name if printerName is given
                    # 2. Match by address if printerAddress is given
                    # 3. If neither is given, match by default IOS name pattern
                    if (
                        (self.printerName and foundName.startswith(self.printerName))
                        or (self.printerAddress and foundAddress == self.printerAddress)
                        or (
                            self.printerName is None
                            and self.printerAddress is None
                            and foundName.startswith("INSTAX-")
                            and foundName.endswith("(IOS)")
                        )
                    ):
                        if peripheral.is_connectable():
                            return peripheral
                        else:
                            self.log(f"Can't connect to printer {foundName} ({foundAddress})")
                secondsTried += scan_duration
                if timeout != 0 and secondsTried >= timeout:
                    return None
        except KeyboardInterrupt:
            self.cancel_print()
            self.disconnect()
            sys.exit()

    def send_led_pattern(
        self, pattern: List[List[int]], speed: int = 5, repeat: int = 255, when: int = 0
    ) -> None:
        """
        Send a LED pattern to the Instax printer.
        - pattern: array of BGR (not RGB!) values to use in animation, e.g. [[255, 0, 0], [0, 255, 0], [0, 0, 255]]
        - speed: time per frame/color: higher is slower animation
        - repeat: 0 = don't repeat (so play once), 1-254 = times to repeat, 255 = repeat forever
        - when: 0 = normal, 1 = on print, 2 = on print completion, 3 = pattern switch
        """

        payload = pack("BBBB", when, len(pattern), speed, repeat)
        for color in pattern:
            payload += pack("BBB", color[0], color[1], color[2])

        packet = self.create_packet(EventType.LED_PATTERN_SETTINGS, payload)
        self.send_packet(packet)

    def prettify_bytearray(self, value: bytes) -> str:
        """Helper funtion to convert a bytearray to a string of hex values."""
        return " ".join([f"{x:02x}" for x in value])

    def create_checksum(self, bytearray: bytes) -> int:
        """Create a checksum for a given packet."""
        return (255 - (sum(bytearray) & 255)) & 255

    def validate_checksum(self, packet: bytes) -> bool:
        """Validate the checksum of a packet."""
        return (sum(packet) & 255) == 255

    def create_packet(self, eventType, payload: bytes = b"") -> bytes:
        """Create a packet to send to the printer."""
        if isinstance(
            eventType, EventType
        ):  # allows passing in an event or a value directly
            eventType = eventType.value

        header = (b"\x41\x62")  # 'Ab' means client to printer, 'aB' means printer to client
        opCode = bytes([eventType[0], eventType[1]])
        packetSize = pack(">H", 7 + len(payload))
        packet = header + packetSize + opCode + payload
        packet += pack("B", self.create_checksum(packet))
        return packet

    def send_packet(self, packet: bytes) -> None:
        """Send a packet to the printer"""
        if self.printerInfo.isDummy:
            return

        if not self.peripheral:
            self.log("no peripheral to send packet to")
            return

        if not self.peripheral.is_connected():
            self.log("peripheral not connected")
            return

        try:
            header, length, op1, op2 = unpack_from(">HHBB", packet)
            try:
                event = EventType((op1, op2))
            except Exception:
                event = "Unknown event to send"

            # print("lock waiting for response for event:", event)
            self.waiting_for_response = True

            # Cut the packet up into smaller parts if needed, then send them one by one
            smallPacketSize = 182
            numberOfParts = ceil(len(packet) / smallPacketSize)
            for subPartIndex in range(numberOfParts):
                subPacket = packet[
                    subPartIndex * smallPacketSize : subPartIndex * smallPacketSize
                    + smallPacketSize
                ]
                self.peripheral.write_command(self.serviceUUID, self.writeCharUUID, subPacket)

            while self.waiting_for_response:
                sleep(0.1)

        except KeyboardInterrupt:
            self.printCancelled = True
            self.cancel_print()
            self.disconnect()
            sys.exit("Print cancelled")

    # def reject_film_cover(self) -> None:
    #     """ Reject the film cover if it's still in the printer """
    #     self.log("Rejecting film cover...")
    #     packet = self.create_packet(EventType.REJECT_FILM_COVER)
    #     self.send_packet(packet)

    def print_image(self, imgSrc: Union[str, bytes]) -> None:
        """
        print an image. Either pass a path to an image (as a string) or pass
        the bytearray to print directly
        """
        self.log(f'printing image "{imgSrc}"')
        if self.printerInfo.photosLeft == 0:
            self.log("No photos left in cartridge; can't print!")
            return

        imgData = imgSrc
        if isinstance(imgSrc, str):  # if it's a path, load the image contents
            try:
                image = Image.open(imgSrc)
            except Exception as e:
                self.log(f"Error opening image from path: {e}")
                return
            imgData = self.pil_image_to_bytes(image)
        elif isinstance(imgSrc, BytesIO):  # if it's a BytesIO object, read from it
            imgSrc.seek(0)  # Go to the start of the BytesIO object
            image = Image.open(imgSrc)
            imgData = self.pil_image_to_bytes(image)

        # The first packet to send is the PRINT_IMAGE_DOWNLOAD_START packet, in which we tell the printer the size
        # of the image data we will be sending. The printer will respond with the chunk size it wants us to use.

        # \x02\x00\x00\x00 payload made of four bytes: pictureType, picturePrintOption, picturePrintOption2, zero
        # TODO: get these from printerInfo instead of hardcoding
        download_start_packet = self.create_packet(
            EventType.PRINT_IMAGE_DOWNLOAD_START,
            b"\x02\x00\x00\x00" + pack(">I", len(imgData)),
        )
        self.send_packet(download_start_packet)

        print("received chunk, so we can split the image")

        self.packetsForPrinting = []

        # divide image data up into chunks of <chunkSize> bytes
        imgDataChunks = [imgData[i:i + self.printerInfo.chunkSize] for i in range(0, len(imgData), self.printerInfo.chunkSize)]
        if len(imgDataChunks[-1]) < self.printerInfo.chunkSize:  # pad the last chunk with zeroes if needed
            imgDataChunks[-1] = imgDataChunks[-1] + bytes(self.printerInfo.chunkSize - len(imgDataChunks[-1]))

        # create a packet from each of our chunks, this includes adding the chunk number
        for index, chunk in enumerate(imgDataChunks):
            imgDataChunks[index] = (pack(">I", index) + chunk)  # add chunk number as int (4 bytes)
            self.packetsForPrinting.append(self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_DATA, imgDataChunks[index]))

        if self.printEnabled:
            self.packetsForPrinting.append(self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_END))
            self.packetsForPrinting.append(self.create_packet(EventType.PRINT_IMAGE))
            self.packetsForPrinting.append(self.create_packet((0, 2), b"\x02"))
        else:
            self.packetsForPrinting.append(self.create_packet(EventType.PRINT_IMAGE_DOWNLOAD_CANCEL))
            self.log("Note: printing is disabled, sending all packets except the actual print command")

        if self.printerInfo.isDummy:
            return

        # send the first packet from our list, the packet handler will take care of the rest
        totalPackages = len(self.packetsForPrinting)
        for idx, packet in enumerate(self.packetsForPrinting):
            if self.printCancelled:
                self.log("Print cancelled")
                break
            # if idx % (totalPackages // 10) == 0:  # every 10 percent
            progress = int(
                (idx / totalPackages) * 50
            )  # Calculate progress for a 50-char wide bar
            loading_bar = f"[{'#' * progress}{'.' * (50 - progress)}] {int((idx / totalPackages) * 100)}%"
            print(loading_bar, end="\r")
            self.logVerbose(f"Img packets left to send: {totalPackages - idx}")
            self.send_packet(packet)

        if self.printEnabled:
            print(f"[{'#' * 50}] 100%")  # Complete loading bar at the end
        else:
            print(
                f"[{'#' * 49}.] 99% - printing disabled"
            )  # Complete loading bar at the end

    def print_services(self) -> None:
        """Get and display and overview of the printer's services and characteristics"""
        self.log("Successfully connected, listing services...")
        services = self.peripheral.services()
        service_characteristic_pair = []
        for service in services:
            for characteristic in service.characteristics():
                service_characteristic_pair.append((service.uuid(), characteristic.uuid()))

        for i, (service_uuid, characteristic) in enumerate(service_characteristic_pair):
            self.log(f"{i}: {service_uuid} {characteristic}")

    def get_printer_orientation(self) -> None:
        """Get the current XYZ orientation of the printer"""
        packet = self.create_packet(EventType.XYZ_AXIS_INFO)
        self.send_packet(packet)

    def get_printer_status(self) -> None:
        """Get the printer's status"""
        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.IMAGE_SUPPORT_INFO.value),
        )
        self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO, pack(">B", InfoType.BATTERY_INFO.value)
        )
        self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.PRINTER_FUNCTION_INFO.value),
        )
        self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.PRINT_HISTORY_INFO.value),
        )
        self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.CAMERA_FUNCTION_INFO.value),
        )
        self.send_packet(packet)

        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.CAMERA_HISTORY_INFO.value),
        )
        self.send_packet(packet)

    def get_printer_info(self) -> None:
        """Get and display the printer's status and info, like photos left and battery level"""
        self.log("Getting function info...")

        try:  # Read model name from Device Information Service
            modelName = self.peripheral.read(
                self.deviceInformationServiceUUID, self.modelNameStringUUID
            )
            self.printerInfo.modelName = modelName.decode("utf-8")
        except Exception as e:
            self.log(f"Error reading device information service UUID: {e}")
            return

        # Get model number
        packet = self.create_packet(
            EventType.DEVICE_INFO_SERVICE, pack(">B", DeviceInfo.MODEL_NUMBER.value)
        )
        self.send_packet(packet)

        # Get serial number
        # packet = self.create_packet(EventType.DEVICE_INFO_SERVICE, pack('>B', DeviceInfo.SERIAL_NUMBER.value))
        # self.send_packet(packet)

        # Get model name, print size, etc.
        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO,
            pack(">B", InfoType.IMAGE_SUPPORT_INFO.value),
        )
        self.send_packet(packet)

        # Get Battery status
        packet = self.create_packet(
            EventType.SUPPORT_FUNCTION_INFO, pack(">B", InfoType.BATTERY_INFO.value)
        )
        self.send_packet(packet)

    def pil_image_to_bytes(self, img: Image.Image) -> bytearray:
        """Convert a PIL image to a bytearray"""
        img_buffer = BytesIO()

        # Convert the image to RGB mode if it's in RGBA mode
        if img.mode == "RGBA":
            img = img.convert("RGB")

        # Resize the image to <imageSize> pixels
        img = img.resize(self.printerInfo.imageSize, Image.Resampling.LANCZOS)

        def save_img_with_quality(quality):
            img_buffer.seek(0)
            img.save(img_buffer, format="JPEG", quality=quality)
            return img_buffer.tell() / 1024

        if self.printerInfo.maxFileSizeKb is not None:
            low_quality, high_quality = 1, 100
            current_quality = 100
            closest_quality = current_quality
            min_target_size_kb = self.printerInfo.maxFileSizeKb * 0.9

            while low_quality <= high_quality:
                output_size_kb = save_img_with_quality(current_quality)
                # self.log(f"current output quality: {current_quality}, current size: {output_size_kb}")

                if (
                    output_size_kb <= self.printerInfo.maxFileSizeKb
                    and output_size_kb >= min_target_size_kb
                ):
                    closest_quality = current_quality
                    break

                if output_size_kb > self.printerInfo.maxFileSizeKb:
                    high_quality = current_quality - 1
                else:
                    low_quality = current_quality + 1

                current_quality = (low_quality + high_quality) // 2
                closest_quality = current_quality

            # Save the image with the closest_quality
            save_img_with_quality(closest_quality)
            self.logVerbose(f"Saved img with quality of {closest_quality}")
        else:
            self.log("No max file size known, saving with default quality")
            img.save(img_buffer, format="JPEG")

        return bytearray(img_buffer.getvalue())


def main(args={}):
    """Example usage of the InstaxBLE class"""
    try:
        instax = InstaxBLE(**args)

        # To prevent misprints during development this script sends the printer
        # all the image data except the final 'go print' command.
        # To enable actual printing uncomment the next line, or pass
        # -p or --print-enabled when calling this script

        # instax.enable_printing()
        instax.connect()

        # Set a rainbow effect to be shown while printing and a pulsating
        # green effect when printing is done
        instax.send_led_pattern(LedPatterns.rainbow, when=1)
        instax.send_led_pattern(LedPatterns.pulseGreen, when=2)

        # you can also read the current accelerometer values if you want
        # while True:
        #     instax.get_printer_orientation()
        #     print(instax.accelerometer)
        #     sleep(.1)

        # send your image (.jpg) to the printer by
        # passing the image_path as an argument when calling
        # this script, or by specifying the path in your code
        if instax.image_path:
            instax.print_image(instax.image_path)

    except Exception as e:
        print(type(e).__name__, __file__, e.__traceback__.tb_lineno)
        instax.log(f"Error: {e}")
    finally:
        instax.disconnect()  # all done, disconnect


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--printer-address")
    parser.add_argument("-n", "--printer-name")
    parser.add_argument("-p", "--print-enabled", action="store_true")
    parser.add_argument("-d", "--dummy-printer", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-i", "--image-path", help="Path to the image file")
    args = parser.parse_args()

    main(vars(args))
