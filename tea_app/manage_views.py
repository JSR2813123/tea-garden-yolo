# tea_app/manage_views.py
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render, redirect
from .forms import PhotoManageForm
from .models import Photo

@login_required
def manage_home(request):
    # 也可以做成 dashboard
    latest_photos = Photo.objects.order_by("-uploaded_at")[:10]
    return render(request, "manage/home.html", {"latest_photos": latest_photos})

@login_required
@permission_required("tea_app.add_photo", raise_exception=True)
def manage_photo_list(request):
    if request.method == "POST":
        form = PhotoManageForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect("manage_home")
    else:
        form = PhotoManageForm()
    return render(request, "manage/photo_create.html", {"form": form})
