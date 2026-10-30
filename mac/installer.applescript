-- "Install Photo Cull.app": the double-click installer in the download. No Terminal: it asks once, shows progress,
-- and says when it's done. The real work is install-mac.sh, carried inside this app (Contents/Resources/payload),
-- because macOS may run a downloaded app from a temporary copy where files beside it can't be seen.

set payload to POSIX path of (path to me) & "Contents/Resources/payload/"
set logFile to POSIX path of (path to library folder from user domain) & "Logs/Photo Cull install.log"
set launcher to POSIX path of (path to library folder from user domain) & "Application Support/Photo Cull/app/mac/launcher.sh"
set work to do shell script "mktemp -d"
set statusFile to work & "/status"
set doneFile to work & "/done"

display dialog "Install Photo Cull on this Mac?" & return & return & "It needs the internet and takes a few minutes the first time. Your photos aren't touched." buttons {"Cancel", "Install"} default button "Install" cancel button "Cancel" with title "Photo Cull" with icon note

do shell script "mkdir -p ~/Library/Logs; (PHOTOCULL_NO_OPEN=1 PHOTOCULL_STATUS=" & quoted form of statusFile & " /bin/bash " & quoted form of (payload & "install-mac.sh") & " > " & quoted form of logFile & " 2>&1; echo $? > " & quoted form of doneFile & ") > /dev/null 2>&1 &"

set progress description to "Installing Photo Cull…"
set progress additional description to "Starting"
set progress total steps to -1
set code to -1
repeat
	delay 1
	try
		set progress additional description to (do shell script "tail -n 1 " & quoted form of statusFile)
	end try
	try
		set code to (do shell script "cat " & quoted form of doneFile) as integer
		exit repeat
	end try
end repeat

if code is not 0 then
	display alert "Photo Cull couldn't install" message "Check the Mac is online, then open this installer again. If it still fails, send Joel the file \"Photo Cull install.log\" from the Logs folder in your Library." as critical
	return
end if

display dialog "Photo Cull is installed." & return & return & "From now on, open it from Applications or Launchpad like any app. It opens in your web browser, and tells you when there's an update." buttons {"Open Photo Cull"} default button 1 with title "Photo Cull" with icon note
do shell script quoted form of launcher & " > /dev/null 2>&1 &"
