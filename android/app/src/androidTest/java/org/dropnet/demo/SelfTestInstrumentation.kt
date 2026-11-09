// SPDX-License-Identifier: GPL-3.0-or-later
package org.dropnet.demo

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle

/** Invoke with adb shell am instrument -w -r org.dropnet.demo.test/org.dropnet.demo.SelfTestInstrumentation; -r exposes the status field. */
class SelfTestInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }
    override fun onStart() {
        val result = Bundle()
        try {
            result.putString("stream", RelaySelfTest.run(targetContext) + "\n")
            result.putString("dropnet_status", "passed")
            finish(Activity.RESULT_OK, result)
        } catch (failure: Throwable) {
            result.putString("stream", "FAIL: ${failure.javaClass.simpleName}: ${failure.message}\n")
            result.putString("dropnet_status", "failed")
            finish(Activity.RESULT_CANCELED, result)
        }
    }
}
