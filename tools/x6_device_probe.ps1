param(
    [Parameter(Mandatory = $true)][long]$ExpectedDiskSize,
    [Parameter(Mandatory = $true)][string]$NormalId,
    [Parameter(Mandatory = $true)][string]$AdfuId
)

$ErrorActionPreference = 'Stop'
$entities = @(Get-CimInstance Win32_PnPEntity)
$normal = @($entities | Where-Object {
    $_.PNPDeviceID -like "USB\$NormalId\*" -and $_.Status -eq 'OK'
} | ForEach-Object {
    [pscustomobject]@{ instance_id = $_.PNPDeviceID; status = $_.Status }
})
$adfu = @($entities | Where-Object {
    $_.PNPDeviceID -like "USB\$AdfuId\*" -and $_.Status -eq 'OK'
} | ForEach-Object {
    [pscustomobject]@{ instance_id = $_.PNPDeviceID; status = $_.Status }
})

$legacy = @(Get-CimInstance Win32_DiskDrive)
$disks = @(Get-Disk | Where-Object {
    $_.BusType -eq 'USB' -and $_.Size -eq $ExpectedDiskSize -and
    $_.FriendlyName -like 'ACTIONS*'
} | ForEach-Object {
    $disk = $_
    $legacyDisk = @($legacy | Where-Object { $_.Index -eq $disk.Number })
    if ($legacyDisk.Count -ne 1 -or
        $legacyDisk[0].PNPDeviceID -notmatch '^USBSTOR\\DISK&VEN_ACTIONS') {
        return
    }
    $usbSerial = ''
    if ($legacyDisk[0].PNPDeviceID -match '\\([^\\]+)&\d+$') {
        $usbSerial = $Matches[1]
    }
    $letters = @(Get-Partition -DiskNumber $disk.Number | Where-Object {
        $_.DriveLetter
    } | ForEach-Object { "$(($_.DriveLetter).ToString().ToUpperInvariant()):" })
    [pscustomobject]@{
        disk_number = $disk.Number
        disk_size = $disk.Size
        disk_serial = $disk.SerialNumber
        usb_serial = $usbSerial
        drives = $letters
    }
})

@{ normal = $normal; adfu = $adfu; disks = $disks } |
    ConvertTo-Json -Compress -Depth 5
