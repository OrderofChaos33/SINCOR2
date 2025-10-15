// Google Docs → Extensions → Apps Script, paste this and run setEightPoint()
function setEightPoint() {
  const body = DocumentApp.getActiveDocument().getBody();
  body.editAsText().setFontSize(8);
}
