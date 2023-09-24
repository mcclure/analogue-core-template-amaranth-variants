# Amaranth Core Template / Screen Test

This is a screen test with synchronized video animation and audio, written by Andi McClure based on a video template by Whitequark. On my system (WSL in Windows 10), these are the build steps, although they will differ on yours:

```
(cd src/fpga/amaranth_core/ && python.exe -m pdm generate) && (cd src/fpga && /mnt/d/intelFPGA_lite/22.1std/quartus/bin64/quartus_sh.exe --flow compile ap_core) && (rm -f ../quartus/reverse/bitstream.rbf_r && ../quartus/reverse/a.out ./src/fpga/output_files/ap_core.rbf ../quartus/reverse/bitstream.rbf_r) && (cd ../quartus/reverse && cmd.exe /c copy bitstream.rbf_r "E:\Cores\test.andi amaranth\bitstream.rbf_r") && (cmd.exe /c copy video.json "E:\Cores\test.andi amaranth\video.json")
```

This assumes JSON files besides video.json have already been configured per the Analogue documentation. Clearer build instructions are forthcoming.

## License

Other than Analogue code (see below), this repo contains Amaranth support code by Whitequark with some additions by andi mcc. This is covered by the BSD0 (public domain like) license [here](src/fpga/amaranth_core/LICENSE.txt). You may want to edit this LICENSE.txt before redistributing your own changes. 

# Analogue Core Template README
This is a template repository for a core which contains all of the core definition JSON files and FPGA starter code.

## Legal
Analogue’s Development program was created to further video game hardware preservation with FPGA technology. Analogue Developers have access to Analogue Pocket I/O’s so Developers can utilize cartridge adapters or interface with other pieces of original or bespoke hardware to support legacy media. Analogue does not support or endorse the unauthorized use or distribution of material protected by copyright or other intellectual property rights.
