# Gson reads and writes the data classes in app.watchman.android.core by reflection (policy, manifest,
# events, pairing). The shrinker must keep their names and fields, and the type signatures Gson walks.
-keep class app.watchman.android.core.** { *; }
-keepattributes Signature, *Annotation*, InnerClasses, EnclosingMethod
-keep class com.google.gson.** { *; }
-dontwarn com.google.gson.**
# ONNX Runtime loads its JNI classes by name.
-keep class ai.onnxruntime.** { *; }
# ML Kit and Google Play services find their components by name at run time (manifest metadata
# and reflection); the shrinker must not remove or rename them, or the code scanner crashes when
# it is created (the first QR pairing attempt on the Pixel, 2026-11-02).
-keep class com.google.mlkit.** { *; }
-keep class com.google.android.gms.internal.mlkit_code_scanner.** { *; }
-keep class com.google.android.gms.internal.mlkit_common.** { *; }
-keep class com.google.firebase.components.** { *; }
-keep class * implements com.google.firebase.components.ComponentRegistrar { *; }
-dontwarn com.google.mlkit.**
