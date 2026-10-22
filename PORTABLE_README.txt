PaveForge for Windows
====================

No Python installation is needed for this portable executable.

1. Download PaveForge-Windows.zip and choose Extract All.
2. Open the extracted PaveForge folder.
3. Double-click PaveForge.exe.

A desktop window starts your local mesh dashboard and opens your browser.
Keep the desktop window open while meshing. Close it to stop the dashboard.
If the browser does not open, click Open dashboard in the desktop window.

Keep the entire PaveForge folder together, including its _internal folder.
Do not move only the EXE out of the folder. The ZIP can be moved between
Windows 10/11 x64 laptops; it runs locally without an account or API key.

In the dashboard:
- Upload your zipped basepoly shapefile.
- Mesh=Triangular creates paving; Exclude/blank stays empty.
- useEleA/use_Ela=1 applies maxEleA/Max_Ela in square metres.
- Turn off the example structure before using your own files.
- Import a structure 2DM if needed, then generate and download the result.
- Use Merge existing 2DM meshes for an existing paving and structure pair.

New in this version:
- All triangles and quads are checked against 5 to 160 degree interior angles.
  Mesh export is blocked on failures; download the angle-failure CSV to locate them.
- Paving near structures defaults to 10 m2 within a selectable 10 m band.
- Merge existing base + road/trench meshes with an 8 m erase-and-fill selection,
  material marker 99, fill area 10 m2, and ETRS89 / UTM 32N (EPSG:25832).
  Selected base cells are removed; far material-99 cells remain unchanged.
- Automatic node welding checks up to 1 mm at full tolerance before export.
- Efficient generation targets 10 degrees; Previous tool targets 26 degrees.
  Fixed acute corners may still fail acceptance while original node XYZ is kept.

merged.2dm already contains the structure; use it as the complete result.
Existing source
pieces outside basepoly are preserved only when Keep outside structure is on;
no bridge is generated between intentional disconnected pieces.

For startup failures, click Open logs in the desktop window.
Logs are stored in your Windows user profile under
%LOCALAPPDATA%\PaveForge\logs. No administrator privileges are required.
