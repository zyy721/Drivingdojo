import torch
import os
from diffusers import StableVideoDiffusionPipeline
from diffusers.utils import load_image
import imageio

from src.model.unet_spatio_temporal_condition_multiview import UNetSpatioTemporalConditionModelMultiview
from utils.custom_video_datasets import VideoNuscenesDataset
from torchvision import transforms
from src.pipeline.pipeline_stable_video_diffusion_multiview import StableVideoDiffusionPipelineMultiview
from PIL import Image
import numpy as np
from diffusers.models import UNetSpatioTemporalConditionModel, AutoencoderKLTemporalDecoder


def concat_images(images, direction='horizontal', pad=0, pad_value=0):
    if len(images) == 1:
        return images[0]
    is_pil = isinstance(images[0], Image.Image)
    if is_pil:
        images = [np.array(image) for image in images]
    if direction == 'horizontal':
        height = max([image.shape[0] for image in images])
        width = sum([image.shape[1] for image in images]) + pad * (len(images) - 1)
        new_image = np.full((height, width, images[0].shape[2]), pad_value, dtype=images[0].dtype)
        begin = 0
        for image in images:
            end = begin + image.shape[1]
            new_image[: image.shape[0], begin:end] = image
            begin = end + pad
    elif direction == 'vertical':
        height = sum([image.shape[0] for image in images]) + pad * (len(images) - 1)
        width = max([image.shape[1] for image in images])
        new_image = np.full((height, width, images[0].shape[2]), pad_value, dtype=images[0].dtype)
        begin = 0
        for image in images:
            end = begin + image.shape[0]
            new_image[begin:end, : image.shape[1]] = image
            begin = end + pad
    else:
        assert False
    if is_pil:
        new_image = Image.fromarray(new_image)
    return new_image

# front, front_right, back_right, back, back_left, front_left

origin_img_list = ['front', 'front_right', 'back_right', 'back', 'back_left', 'front_left']
idx_permute_img = [5, 0, 1, 2, 3, 4]
# permute_img_list = []
# for idx in idx_permute_img:
#     permute_img_list.append(origin_img_list[idx])

# Setting
# length = 14
# w = 1024
# h = 576

dataset_name = '../../../data/sample_nusc_video_all_cam_val.pkl'

length = 3
w = 576
h = 320
interval = 1
val_batch_size = 1
dataloader_num_workers = 2

# model path
pretrained_model_path = 'demo_model/img2video_1024_14f'
# model_path = '../../work_dirs/nusc_fsdp_svd_front_576320_30f/checkpoint-0'
model_path = '../../work_dirs/nusc_deepspeed_svd_front_576320_30f/checkpoint-0'

# initial image path
# img_path = 'demo_img/0010_CameraFpgaP0H120.jpg'

output_folder = './output'
os.makedirs(output_folder,exist_ok=True)

# pipe = StableVideoDiffusionPipeline.from_pretrained(
#     model_path, torch_dtype=torch.float16, variant="fp16"
# )

pipe_param = {}
# unet_path = os.path.join(model_path, 'unet')
# unet = UNetSpatioTemporalConditionModelMultiview.from_pretrained(unet_path, torch_dtype=torch.float16)

unet_origin = UNetSpatioTemporalConditionModel.from_pretrained(pretrained_model_path, subfolder="unet")
unet_param = {
    "trainable_state": "only_new",  # only_new or all
    "neighboring_view_pair": {0: [5, 1],
                              1: [0, 2],
                              2: [1, 3],
                              3: [2, 4],
                              4: [3, 5],
                              5: [4, 0]},
    # "neighboring_view_pair": {0: [2, 1],
    #                             1: [0, 2],
    #                             2: [1, 0]},
    "neighboring_attn_type": "add",
    "zero_module_type": "zero_linear",
    "crossview_attn_type": 'basic',
    "img_size": [320, 576],

    # "nframes_past": nframes_past,

}
unet = UNetSpatioTemporalConditionModelMultiview.from_unet_spatio_temporal_condition(unet_origin, **unet_param)

# state_dict = torch.load(f'{model_path}/pytorch_model.bin', map_location="cpu")
# unet.load_state_dict(state_dict, strict=True)
# del state_dict

from deepspeed.utils.zero_to_fp32 import get_fp32_state_dict_from_zero_checkpoint
state_dict = get_fp32_state_dict_from_zero_checkpoint(model_path)
unet.load_state_dict(state_dict, strict=True)
del state_dict

unet.eval()
pipe_param['unet'] = unet

pipe = StableVideoDiffusionPipelineMultiview.from_pretrained(
    pretrained_model_path, **pipe_param, torch_dtype=torch.float16, variant="fp16"
)
pipe.enable_model_cpu_offload()

# image = load_image(img_path)

val_transforms = transforms.Compose(
    [
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5]),
    ]
)

val_dataset = VideoNuscenesDataset(
    data_root=dataset_name,
    video_transforms=val_transforms,
    # tokenizer=tokenizer,
    video_length=length,
    interval = interval,
    img_size= (w, h),

    multi_view=True,

)

# DataLoaders creation:
val_dataloader = torch.utils.data.DataLoader(
    val_dataset,
    shuffle=False,
    batch_size=val_batch_size,
    # batch_size=2,

    num_workers=dataloader_num_workers,
)


generator = torch.manual_seed(42)

idx = 0

for batch_dict in val_dataloader:

    batch_dict["pixel_values"] = batch_dict["pixel_values"].reshape(-1, *batch_dict["pixel_values"].shape[-4:])

    # frames = pipe(image, width=w, height=h,num_frames=length, num_inference_steps=25, noise_aug_strength=0.01, fps = 5, generator=generator).frames[0]
    frames = pipe(batch_dict, width=w, height=h,num_frames=length, num_inference_steps=25, noise_aug_strength=0, fps = 2, generator=generator).frames

    # export_path = os.path.join(output_folder, 'test.gif')

    # # export to gif
    # imageio.mimsave(export_path, frames, format='GIF', duration=200, loop=0)

    all_multiview_imgs = []        
    for idx_frame in range(length):
        cur_multiview_imgs = []
        for idx_cam in idx_permute_img:
            cur_multiview_imgs.append(frames[idx_cam][idx_frame])
        cur_row_first = concat_images(cur_multiview_imgs[:3], pad=0)
        cur_row_last = concat_images(cur_multiview_imgs[3:], pad=0)
        cat_cur_multiview_imgs = concat_images([cur_row_first, cur_row_last], direction='vertical')

        all_multiview_imgs.append(cat_cur_multiview_imgs)

        cat_cur_multiview_imgs.save('{:06d}_{}.jpg'.format(idx, idx_frame))

    imageio.mimsave(os.path.join(output_folder, '{:06d}.mp4'.format(idx)), all_multiview_imgs, fps=2)
    idx += 1


