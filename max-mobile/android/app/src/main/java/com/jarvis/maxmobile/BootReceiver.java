package com.jarvis.maxmobile;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

public class BootReceiver extends BroadcastReceiver {

    private static final String TAG = "MaxBootReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent != null && (
                Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) ||
                "android.intent.action.QUICKBOOT_POWERON".equals(intent.getAction()) ||
                Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())
        )) {
            Log.d(TAG, "⚡ BOOT_COMPLETED detected! Auto-starting MAX Background Service...");
            Intent serviceIntent = new Intent(context, MaxBackgroundService.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(serviceIntent);
            } else {
                context.startService(serviceIntent);
            }
        }
    }
}
